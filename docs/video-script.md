# Technical review video script (target: 2:40)

Keep the camera on and the final link publicly viewable.

## 0:00-0:50 - System design and request lifecycle

Show `architecture.svg`.

> This is an asynchronous transaction-processing API. FastAPI owns the HTTP
> boundary; PostgreSQL is the system of record; Redis is only the queue; and
> Celery owns long-running work. One Compose command starts all four services.
>
> On upload, the API streams and validates the CSV, creates a pending Job, and
> sends only its UUID to Redis. It returns 202 immediately. A Celery worker
> claims that ID, marks the Job processing, loads the shared upload, performs
> cleaning and anomaly detection, calls Gemini in category batches plus one
> summary call, then atomically writes transactions and the JobSummary. The
> client polls status and retrieves results from PostgreSQL.

## 0:50-1:10 - Why these choices

> FastAPI provides typed OpenAPI endpoints with little ceremony. SQLAlchemy
> keeps persistence explicit and testable. Celery gives retries, late
> acknowledgements, concurrency controls, and mature Redis support. I calculate
> money totals and anomaly counts deterministically; the LLM supplies
> classification and narrative, so model output cannot corrupt financial
> arithmetic. Missing keys or exhausted retries are visible as llm_failed but
> do not fail the whole job.

Briefly show Swagger upload/status/results and one completed response.

## 1:10-1:50 - Exact breaking points at 100x

> At 100x, the first break is worker memory because each CSV is loaded in one
> process. The named upload volume also binds workers to a shared filesystem.
> Next are PostgreSQL connections from API and worker replicas, Redis queue
> depth, and Gemini rate limits. Finally, returning an unpaginated transaction
> list makes API memory and response size grow linearly. The current automatic
> create-all schema step is convenient here but is not safe for production
> migrations.

## 1:50-2:40 - Enterprise iteration and trade-offs

> I would stream uploads to S3-compatible object storage, parse in bounded
> chunks, and make tasks idempotent using a content hash. I would split queues:
> a scalable cleaning queue and a rate-limited LLM queue, with dead-lettering
> and queue-lag metrics. PostgreSQL would use Alembic migrations, PgBouncer,
> read replicas for polling, table partitioning, and retention policies.
> Results would be paginated and summaries cached.
>
> Those choices remove host affinity and make backpressure observable, but add
> object-storage lifecycle management, eventual consistency, more failure
> states, and higher operational cost. For the assignment's 90-row workload,
> the submitted design stays intentionally simpler while preserving clean
> boundaries for that evolution.

Stop recording before 3:00.
