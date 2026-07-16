from __future__ import annotations

from processors.podcast import PodcastProcessor
from processors.tweet import TweetOrganizer

from .registry import SourceRegistry
from .runtime import Runtime, utc_iso


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

    def run(self, limit: int = 2) -> int:
        with self.runtime.lock("worker"):
            self.runtime.update_operation("worker", "default", outcome="running", startedAt=utc_iso())
            self.registry.recover_stale_jobs()
            jobs = self.registry.claim_jobs(limit)
            completed = 0
            for job in jobs:
                try:
                    if job.processor == "podcast":
                        result = self.podcast.process(
                            job.canonical_url, job.metadata,
                            collection_subdir=job.collection_subdir,
                        )
                    elif job.processor == "tweet_organizer":
                        result = self.tweet.process(job.metadata, {
                            "origin": job.metadata.pop("origin", "list"),
                            "lists": job.metadata.pop("lists", []),
                        }, collection_subdir=job.collection_subdir)
                    else:
                        raise ValueError(f"unsupported processor: {job.processor}")
                    self.registry.finish_job(job.id, result)
                    completed += 1
                except Exception as error:
                    self.registry.finish_job(job.id, error=str(error))
                    self.runtime.log(f"job {job.id} failed: {error}")
            self.runtime.update_operation(
                "worker", "default", outcome="complete", finishedAt=utc_iso(), completed=completed
            )
            return completed
