"""Queue worker. Run any number of these; they coordinate only through the database."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
import uuid
from datetime import UTC, datetime, timedelta

from prometheus_client import Counter, Gauge, Histogram
from sqlalchemy import and_, func, or_, select, update

from aether import db as dbm
from aether.config import get_settings
from aether.models import Job, Run, Trial
from aether.services import runs as run_svc
from aether.services.suites import run_generation_job

log = logging.getLogger("aether.worker")

TRIALS = Counter("aether_trials_processed_total", "Trials processed", ["status"])
TRIAL_SECONDS = Histogram("aether_trial_duration_seconds", "Wall time per trial", buckets=(1, 2, 5, 10, 20, 40, 80, 160, 320))
INFLIGHT = Gauge("aether_worker_inflight", "Trials and jobs in progress")
JOBS = Counter("aether_jobs_processed_total", "Background jobs processed", ["kind", "status"])


def utcnow() -> datetime:
    return datetime.now(UTC)


def _is_pg() -> bool:
    return dbm.get_engine().dialect.name == "postgresql"


class Worker:
    def __init__(self, concurrency: int | None = None, worker_id: str | None = None):
        s = get_settings()
        self.concurrency = concurrency or s.worker_concurrency
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.lease = timedelta(seconds=s.job_lease_s)
        self.poll = s.worker_poll_interval_s
        self.max_attempts = s.max_job_attempts
        self.running: set[asyncio.Task] = set()
        self._last_sweep = 0.0

    # ---------- claiming ----------
    async def _claim_trials(self, n: int) -> list[str]:
        if n <= 0:
            return []
        now = utcnow()
        claimable = or_(Trial.status == "queued", and_(Trial.status == "running", Trial.lease_until < now))
        async with dbm.sessionmaker()() as db:
            # Fair share: split free slots across active runs so one large run cannot starve small ones.
            active = (
                await db.execute(
                    select(Trial.run_id)
                    .join(Run, Run.id == Trial.run_id)
                    .where(Run.status.in_(("queued", "running")), claimable)
                    .group_by(Trial.run_id)
                    .order_by(func.min(Trial.created_at))
                    .limit(50)
                )
            ).scalars().all()
            if not active:
                return []
            share = max(1, -(-n // len(active)))
            rows = []
            for rid in active:
                if len(rows) >= n:
                    break
                q = select(Trial.id, Trial.run_id).where(Trial.run_id == rid, claimable).order_by(Trial.trial_index, Trial.created_at).limit(min(share, n - len(rows)))
                if _is_pg():
                    q = q.with_for_update(skip_locked=True)
                rows.extend((await db.execute(q)).all())
            if len(rows) < n:
                taken = {r[0] for r in rows}
                q = (
                    select(Trial.id, Trial.run_id)
                    .join(Run, Run.id == Trial.run_id)
                    .where(Run.status.in_(("queued", "running")), claimable)
                    .order_by(Trial.created_at, Trial.trial_index)
                    .limit(n - len(rows) + len(taken))
                )
                if _is_pg():
                    q = q.with_for_update(skip_locked=True, of=Trial)
                rows.extend(r for r in (await db.execute(q)).all() if r[0] not in taken)
                rows = rows[:n]
            claimed: list[str] = []
            run_ids: set[str] = set()
            for tid, rid in rows:
                res = await db.execute(
                    update(Trial)
                    .where(Trial.id == tid, claimable)
                    .values(status="running", worker_id=self.worker_id, lease_until=now + self.lease, attempts=Trial.attempts + 1, started_at=now)
                )
                if res.rowcount == 1:
                    claimed.append(tid)
                    run_ids.add(rid)
            if run_ids:
                await db.execute(update(Run).where(Run.id.in_(run_ids), Run.status == "queued").values(status="running", started_at=now))
            await db.commit()
            return claimed

    async def _claim_jobs(self, n: int) -> list[str]:
        if n <= 0:
            return []
        now = utcnow()
        claimable = or_(Job.status == "queued", and_(Job.status == "running", Job.lease_until < now))
        async with dbm.sessionmaker()() as db:
            q = select(Job.id).where(claimable).order_by(Job.created_at).limit(n)
            if _is_pg():
                q = q.with_for_update(skip_locked=True)
            ids = (await db.execute(q)).scalars().all()
            claimed = []
            for jid in ids:
                res = await db.execute(
                    update(Job).where(Job.id == jid, claimable).values(status="running", worker_id=self.worker_id, lease_until=now + self.lease, attempts=Job.attempts + 1)
                )
                if res.rowcount == 1:
                    claimed.append(jid)
            await db.commit()
            return claimed

    # ---------- execution ----------
    async def _heartbeat(self, model, row_id: str) -> None:
        while True:
            await asyncio.sleep(max(5.0, self.lease.total_seconds() / 3))
            async with dbm.sessionmaker()() as db:
                await db.execute(
                    update(model).where(model.id == row_id, model.worker_id == self.worker_id, model.status == "running").values(lease_until=utcnow() + self.lease)
                )
                await db.commit()

    async def _do_trial(self, trial_id: str) -> None:
        hb = asyncio.create_task(self._heartbeat(Trial, trial_id))
        started = time.perf_counter()
        status = "done"
        run_id = None
        try:
            async with dbm.sessionmaker()() as db:
                trial = await db.get(Trial, trial_id)
                if trial is None:
                    return
                run_id = trial.run_id
                if trial.attempts > self.max_attempts:
                    trial.status, trial.error, trial.passed = "error", "trial was abandoned by crashed workers too many times", False
                    trial.failure_category = "Crashed or gave no answer"
                    await db.commit()
                    status = "error"
                else:
                    await run_svc.process_trial(db, trial_id)
                    await db.refresh(trial)
                    status = trial.status
        except Exception as exc:  # noqa: BLE001
            status = "error"
            log.exception("trial failed", extra={"trial_id": trial_id})
            async with dbm.sessionmaker()() as db:
                await db.execute(
                    update(Trial).where(Trial.id == trial_id).values(
                        status="error", passed=False, error=f"{type(exc).__name__}: {exc}"[:2000], failure_category="Crashed or gave no answer", finished_at=utcnow(), lease_until=None
                    )
                )
                await db.commit()
        finally:
            hb.cancel()
            TRIALS.labels(status).inc()
            TRIAL_SECONDS.observe(time.perf_counter() - started)
        if run_id:
            async with dbm.sessionmaker()() as db:
                await run_svc.maybe_finish(db, run_id)

    async def _do_job(self, job_id: str) -> None:
        hb = asyncio.create_task(self._heartbeat(Job, job_id))
        kind = "?"
        try:
            async with dbm.sessionmaker()() as db:
                job = await db.get(Job, job_id)
                if job is None:
                    return
                kind = job.kind
                try:
                    if job.kind == "generate_suite":
                        result = await run_generation_job(db, job)
                    elif job.kind == "rescore_run":
                        result = await run_svc.run_rescore_job(db, job)
                    else:
                        raise ValueError(f"unknown job kind {job.kind}")
                    job = await db.get(Job, job_id)
                    job.status, job.result, job.finished_at, job.lease_until = "done", result, utcnow(), None
                    await db.commit()
                    JOBS.labels(kind, "done").inc()
                except Exception as exc:  # noqa: BLE001
                    log.exception("job failed", extra={"job_id": job_id})
                    await db.rollback()
                    job = await db.get(Job, job_id)
                    job.status, job.error, job.finished_at, job.lease_until = "failed", f"{type(exc).__name__}: {exc}"[:2000], utcnow(), None
                    await db.commit()
                    JOBS.labels(kind, "failed").inc()
        finally:
            hb.cancel()

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self.running.add(t)
        INFLIGHT.set(len(self.running))

        def _done(task: asyncio.Task) -> None:
            self.running.discard(task)
            INFLIGHT.set(len(self.running))

        t.add_done_callback(_done)

    async def _sweep(self) -> None:
        """Finalize runs whose trials are all finished but that were never closed (e.g. a worker died mid-finalize)."""
        async with dbm.sessionmaker()() as db:
            stuck = (await db.execute(select(Run.id).where(Run.status.in_(("queued", "running"))))).scalars().all()
            for rid in stuck:
                left = (await db.execute(select(func.count()).select_from(Trial).where(Trial.run_id == rid, Trial.status.in_(("queued", "running"))))).scalar()
                if not left:
                    await run_svc.maybe_finish(db, rid)
            stale = utcnow() - timedelta(minutes=15)
            await db.execute(update(Run).where(Run.status == "analyzing", Run.analyzing_since < stale).values(status="running"))
            await db.commit()

    async def tick(self) -> int:
        free = self.concurrency - len(self.running)
        launched = 0
        for jid in await self._claim_jobs(min(2, free)):
            self._spawn(self._do_job(jid))
            launched += 1
        free = self.concurrency - len(self.running)
        for tid in await self._claim_trials(free):
            self._spawn(self._do_trial(tid))
            launched += 1
        if time.monotonic() - self._last_sweep > 30:
            self._last_sweep = time.monotonic()
            await self._sweep()
        return launched

    async def drain(self, timeout: float = 600) -> None:
        """Process until the queue is empty (used by tests and the CLI)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            launched = await self.tick()
            if not launched and not self.running:
                await self._sweep()
                if not await self._claim_preview():
                    return
            await asyncio.sleep(0.05 if self.running else self.poll / 5)
        raise TimeoutError("queue did not drain in time")

    async def _claim_preview(self) -> bool:
        async with dbm.sessionmaker()() as db:
            t = (await db.execute(select(func.count()).select_from(Trial).join(Run).where(Run.status.in_(("queued", "running")), Trial.status.in_(("queued", "running"))))).scalar()
            j = (await db.execute(select(func.count()).select_from(Job).where(Job.status.in_(("queued", "running"))))).scalar()
            r = (await db.execute(select(func.count()).select_from(Run).where(Run.status.in_(("queued", "running", "analyzing"))))).scalar()
            return bool(t or j or r)

    async def run_forever(self, stop: asyncio.Event) -> None:
        log.info("worker started", extra={"worker_id": self.worker_id, "concurrency": self.concurrency})
        while not stop.is_set():
            try:
                launched = await self.tick()
            except Exception:  # noqa: BLE001 - keep the loop alive through DB hiccups
                log.exception("worker tick failed")
                launched = 0
            if not launched:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.poll)
                except TimeoutError:
                    pass
        log.info("worker stopping; waiting for in-flight work", extra={"inflight": len(self.running)})
        if self.running:
            await asyncio.wait(self.running, timeout=60)
