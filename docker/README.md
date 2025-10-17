# Docker Setup for GRVT-Paradex Hedge Mode

## Problem

GRVT and Paradex SDKs have **incompatible dependencies**:

- **GRVT SDK** requires `websockets == 13.1`
- **Paradex SDK** requires `websockets >= 15.0`

These versions cannot coexist in the same Python environment.

## Solution

Use Docker containers to isolate each SDK with its own dependencies:

```
┌─────────────────────────────────────────────┐
│         Hedge Mode Orchestrator             │
│         (Your local machine)                │
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

## Prerequisites

- Docker and Docker Compose installed
- GRVT and Paradex API credentials in `.env` file

## Setup

### 1. Configure Environment Variables

Create a `.env` file in the project root:

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

### 2. Build and Start Services

```bash
# Build Docker images
docker-compose build

# Start services in detached mode
docker-compose up -d

# Check service health
docker-compose ps
```

Expected output:
```
NAME                IMAGE              STATUS             PORTS
grvt-service        grvt:latest        Up (healthy)       0.0.0.0:8001->8001/tcp
paradex-service     paradex:latest     Up (healthy)       0.0.0.0:8002->8002/tcp
```

### 3. Test Services

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

## API Endpoints

### GRVT Service (Port 8001)

#### Initialize
```bash
POST http://localhost:8001/init
{
  "ticker": "BTC",
  "quantity": 0.01,
  "direction": "buy"
}
```

#### Connect WebSocket
```bash
POST http://localhost:8001/connect
```

#### Get BBO Prices
```bash
GET http://localhost:8001/bbo/{contract_id}
```

#### Place Open Order
```bash
POST http://localhost:8001/order/open
{
  "contract_id": "BTC_USDT_Perp",
  "quantity": 0.01,
  "direction": "buy"
}
```

#### Get Order Info
```bash
GET http://localhost:8001/order/{order_id}
```

#### Get Active Orders
```bash
GET http://localhost:8001/orders/active/{contract_id}
```

#### Get Position
```bash
GET http://localhost:8001/position
```

### Paradex Service (Port 8002)

Same endpoints as GRVT, but on port 8002.

## Usage in Hedge Mode

The hedge mode script communicates with both services via HTTP:

```python
import requests

# Initialize GRVT
grvt_response = requests.post('http://localhost:8001/init', json={
    "ticker": "BTC",
    "quantity": 0.01,
    "direction": "buy"
})

# Initialize Paradex
paradex_response = requests.post('http://localhost:8002/init', json={
    "ticker": "BTC",
    "quantity": 0.01,
    "direction": "sell"
})

# Place GRVT order
grvt_order = requests.post('http://localhost:8001/order/open', json={
    "contract_id": "BTC_USDT_Perp",
    "quantity": 0.01,
    "direction": "buy"
})

# Immediately hedge on Paradex
paradex_order = requests.post('http://localhost:8002/order/open', json={
    "contract_id": "BTC-USD-PERP",
    "quantity": 0.01,
    "direction": "sell"
})
```

## Logs and Debugging

### View container logs
```bash
# GRVT logs
docker-compose logs -f grvt

# Paradex logs
docker-compose logs -f paradex

# Both services
docker-compose logs -f
```

### Restart services
```bash
docker-compose restart
```

### Stop services
```bash
docker-compose stop
```

### Remove containers
```bash
docker-compose down
```

### Rebuild after code changes
```bash
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

## Troubleshooting

### Services not starting

Check logs:
```bash
docker-compose logs
```

### Connection refused

Ensure services are running:
```bash
docker-compose ps
```

Check health status:
```bash
curl http://localhost:8001/health
curl http://localhost:8002/health
```

### Environment variables not loading

Verify `.env` file exists in project root:
```bash
cat .env
```

Restart services:
```bash
docker-compose down
docker-compose up -d
```

## Production Deployment

For production, consider:

1. **Use Docker Swarm or Kubernetes** for orchestration
2. **Add monitoring** (Prometheus, Grafana)
3. **Configure logging** (ELK stack, Splunk)
4. **Set resource limits** in docker-compose.yml:
   ```yaml
   services:
     grvt:
       deploy:
         resources:
           limits:
             cpus: '1'
             memory: 1G
   ```
5. **Use secrets management** instead of `.env` file
6. **Enable HTTPS** with reverse proxy (Nginx, Traefik)

## Architecture Benefits

✅ **Isolated Dependencies**: Each service runs with its own Python environment
✅ **Scalable**: Easy to add more exchange services
✅ **Maintainable**: Clear separation of concerns
✅ **Testable**: Each service can be tested independently
✅ **Production-Ready**: Can be deployed to any Docker-capable environment

## License

Same as main project.
