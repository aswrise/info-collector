from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from processors.podcast import PodcastProcessor
from processors.tweet import TweetOrganizer

from .registry import SourceRegistry
from .runtime import Runtime, utc_iso


MAX_CONCURRENT_JOBS = 4


class Worker:
    def __init__(
        self,
        registry: SourceRegistry,
        podcast: PodcastProcessor | None = None,
        tweet: TweetOrganizer | None = None,
        runtime: Runtime | None = None,
    ):
        self.registry = registry
        self.podcast = podcast or PodcastProcessor()
        self.tweet = tweet or TweetOrganizer()
        self.runtime = runtime or Runtime(registry.path.parent)

    def run(self) -> int:
        with self.runtime.lock("worker"):
            self.runtime.update_operation(
                "worker", "default", outcome="running", startedAt=utc_iso(),
                concurrency=MAX_CONCURRENT_JOBS,
            )
            self.registry.recover_stale_jobs(0)
            completed = 0
            active = {}
            with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS) as executor:
                while True:
                    slots = MAX_CONCURRENT_JOBS - len(active)
                    if slots:
                        for job in self.registry.claim_jobs(slots):
                            active[executor.submit(self._process, job)] = job
                    if not active:
                        break
                    done, _ = wait(active, return_when=FIRST_COMPLETED)
                    for future in done:
                        job = active.pop(future)
                        try:
                            self.registry.finish_job(job.id, future.result())
                            completed += 1
                        except Exception as error:
                            self.registry.finish_job(job.id, error=str(error))
                            self.runtime.log(f"job {job.id} failed: {error}")
            self.runtime.update_operation(
                "worker", "default", outcome="complete", finishedAt=utc_iso(), completed=completed
            )
            return completed

    def _process(self, job):
        if job.processor == "podcast":
            return self.podcast.process(
                job.canonical_url, job.metadata,
                collection_subdir=job.collection_subdir,
            )
        if job.processor == "tweet_organizer":
            return self.tweet.process(job.metadata, {
                "origin": job.metadata.pop("origin", "list"),
                "lists": job.metadata.pop("lists", []),
            }, collection_subdir=job.collection_subdir)
        raise ValueError(f"unsupported processor: {job.processor}")
