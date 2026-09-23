#!/bin/bash

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

cd "$DIR"

COMMAND="docker compose"

echo "Running Pulsar with $COMMAND from $DIR..."

sudo mkdir -p ./data/zookeeper ./data/bookkeeper
sudo chown -R 10000 data

docker exec -it emerald-broker bash bin/pulsar-admin namespaces create public/default
docker exec -it emerald-broker bash bin/pulsar-admin namespaces list public


$COMMAND up -d

echo "Pulsar launched"