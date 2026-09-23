#!/bin/bash

echo "Stopping dev env..."

docker compose -f ./postgres/docker-compose.yaml down
docker compose -f ./pulsar/docker-compose.yaml down
docker compose -f ./seaweed/docker-compose.yaml down
docker compose -f ./redis/docker-compose.yaml down

docker network rm emerald-infra-net 2>/dev/null || true

echo "Dev env stopped."