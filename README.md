# Fraud Detection Service

Real-time fraud detection service powered by pylitmus rules engine.

## Features

- Rule-based fraud detection
- Real-time transaction scoring
- Kafka integration for event processing
- Async notifications via Celery
- RESTful API with FastAPI

## Development

```bash
# Start all services
make dev.up

# Run tests
make test

# Check health
make dev.health
```

## API Documentation

Once running, visit:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
