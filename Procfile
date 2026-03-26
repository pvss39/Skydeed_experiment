# Procfile — Railway reads this to know what processes to run.
#
# Railway runs ONE process per service. Create 3 services in Railway:
#   1. "api"      → runs the FastAPI web server
#   2. "worker"   → runs the Celery background worker
#   3. "beat"     → runs Celery Beat (scheduler that queues periodic tasks)
#
# In Railway: each service points to same Git repo, but uses a different
# start command. Set the start command in Railway → Service → Settings → Deploy.

web: uvicorn api.main:app --host 0.0.0.0 --port $PORT
worker: celery -A workers.tasks worker --loglevel=info --concurrency=2
beat: celery -A workers.tasks beat --loglevel=info
