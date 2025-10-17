# GRVT-Paradex Hedge Mode Setup

## Problem Statement

GRVT and Paradex Python SDKs have **incompatible dependencies** that cannot coexist in the same virtual environment:

- **GRVT SDK** requires `websockets == 13.1`
- **Paradex SDK** requires `websockets >= 15.0`

## Solution: Docker-based Architecture

We use Docker containers to isolate each SDK with its own dependencies, and communicate via REST APIs.

```
┌─────────────────────────────────────────────┐
│     hedge_mode_grvt_paradex.py              │
│     (Your local machine)                     │
│     Uses httpx to call REST APIs            │
└───────────┬─────────────────┬───────────────┘
            │                 │
    ┌───────▼────────┐  ┌────▼──────────┐
    │  GRVT Container │  │ Paradex       │
    │  Port: 8001     │  │ Container     │
    │  websockets     │  │ Port: 8002    │
    │  13.1           │  │ websockets    │
    │                 │  │ 15.0          │
    └─────────────────┘  └───────────────┘
```

## Step-by-Step Setup

### 1. Install Docker

Make sure Docker and Docker Compose are installed on your system:

```bash
# Check Docker installation
docker --version
docker-compose --version
```

If not installed, visit: https://docs.docker.com/get-docker/

### 2. Configure Environment Variables

Edit your `.env` file to include GRVT and Paradex credentials:

```bash
# GRVT Configuration
GRVT_TRADING_ACCOUNT_ID=your_account_id
GRVT_PRIVATE_KEY=your_private_key
GRVT_API_KEY=your_api_key
GRVT_ENVIRONMENT=prod  # or testnet

# Paradex Configuration
PARADEX_L1_ADDRESS=your_l1_address
PARADEX_L2_PRIVATE_KEY=your_l2_private_key_hex
PARADEX_L2_ADDRESS=your_l2_address
PARADEX_ENVIRONMENT=prod  # or testnet
```

### 3. Build and Start Docker Services

```bash
# Build Docker images (first time only, or after code changes)
docker-compose build

# Start services in detached mode
docker-compose up -d

# Check that services are running
docker-compose ps
```

Expected output:
```
NAME                IMAGE              STATUS             PORTS
grvt-service        grvt:latest        Up (healthy)       0.0.0.0:8001->8001/tcp
paradex-service     paradex:latest     Up (healthy)       0.0.0.0:8002->8002/tcp
```

### 4. Verify Services Health

```bash
# Test GRVT service
curl http://localhost:8001/health

# Test Paradex service
curl http://localhost:8002/health
```

Expected response:
```json
{
  "status": "healthy",
  "service": "grvt",
  "initialized": false
}
```

### 5. Install Python Dependencies (Local Machine)

Install httpx in your local environment:

```bash
# Activate your virtual environment
source venv/bin/activate  # or your venv name

# Install httpx
pip install httpx>=0.27.0

# Or install all requirements
pip install -r requirements.txt
```

### 6. Run Hedge Mode

```bash
# Run hedge mode with GRVT-Paradex
python hedge_mode.py --exchange grvt --ticker BTC --size 0.001 --iter 10
```

## Usage Examples

### Test Mode (Testnet)

```bash
# Set testnet in .env
GRVT_ENVIRONMENT=testnet
PARADEX_ENVIRONMENT=testnet

# Run with small size for testing
python hedge_mode.py --exchange grvt --ticker BTC --size 0.001 --iter 1
```

### Production Mode

```bash
# Set production in .env
GRVT_ENVIRONMENT=prod
PARADEX_ENVIRONMENT=prod

# Run with real size
python hedge_mode.py --exchange grvt --ticker BTC --size 0.01 --iter 10
```

## Monitoring and Logs

### View Logs

```bash
# View hedge bot logs
tail -f logs/grvt_BTC_hedge_mode_docker_log.txt

# View trade history
cat logs/grvt_BTC_hedge_mode_docker_trades.csv

# View Docker container logs
docker-compose logs -f grvt      # GRVT service logs
docker-compose logs -f paradex   # Paradex service logs
docker-compose logs -f           # Both services
```

### Check Container Status

```bash
# Check if containers are running
docker-compose ps

# Check resource usage
docker stats
```

## Troubleshooting

### Services Not Starting

```bash
# Check logs for errors
docker-compose logs

# Rebuild images
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

### Connection Refused

```bash
# Make sure services are running
docker-compose ps

# Check health endpoints
curl http://localhost:8001/health
curl http://localhost:8002/health

# Restart services
docker-compose restart
```

### Environment Variables Not Loading

```bash
# Verify .env file exists
ls -la .env

# Check .env content
cat .env

# Restart services to pick up new env vars
docker-compose down
docker-compose up -d
```

### Port Already in Use

If ports 8001 or 8002 are already in use:

```bash
# Option 1: Stop the conflicting service
# Find what's using the port
lsof -i :8001
lsof -i :8002

# Option 2: Change ports in docker-compose.yml
# Edit docker-compose.yml and change:
#   ports:
#     - "8001:8001"  # Change first 8001 to something else
# Then set GRVT_SERVICE_URL environment variable
export GRVT_SERVICE_URL=http://localhost:YOUR_NEW_PORT
```

## Stopping Services

```bash
# Stop services (containers remain)
docker-compose stop

# Stop and remove containers
docker-compose down

# Stop, remove containers, and remove images
docker-compose down --rmi all
```

## Architecture Benefits

✅ **Isolated Dependencies**: Each SDK runs in its own environment
✅ **No Version Conflicts**: websockets 13.1 and 15.0 can coexist
✅ **Scalable**: Easy to add more exchange services
✅ **Maintainable**: Clear separation of concerns
✅ **Testable**: Each service can be tested independently
✅ **Production-Ready**: Deploy anywhere Docker runs

## Performance Considerations

- **Latency**: HTTP calls add ~1-5ms overhead vs direct SDK calls
- **Network**: Services run on local Docker network (low latency)
- **Resources**: Each container uses ~100-200MB RAM

## Security

- Services only accessible on localhost by default
- API keys passed via environment variables (not in code)
- For production deployment, consider:
  - Using Docker secrets instead of .env
  - Adding authentication to service APIs
  - Deploying behind reverse proxy with HTTPS

## Advanced Configuration

### Custom Service URLs

```bash
# Set custom URLs if running services elsewhere
export GRVT_SERVICE_URL=http://custom-host:8001
export PARADEX_SERVICE_URL=http://custom-host:8002

python hedge_mode.py --exchange grvt --ticker BTC --size 0.01 --iter 10
```

### Resource Limits

Edit `docker-compose.yml` to add resource limits:

```yaml
services:
  grvt:
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 1G
        reservations:
          cpus: '0.5'
          memory: 512M
```

## Support

For issues or questions:
1. Check Docker logs: `docker-compose logs`
2. Check service health: `curl http://localhost:8001/health`
3. See detailed README: `docker/README.md`
4. Open an issue on GitHub

## License

Same as main project.
