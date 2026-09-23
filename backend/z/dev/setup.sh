#!/bin/bash

echo "Running dev env..."

chmod +x ./postgres/run.sh
chmod +x ./pulsar/run.sh
chmod +x ./seaweed/run.sh
chmod +x ./redis/run.sh

docker network create emerald-infra-net

./postgres/run.sh
./pulsar/run.sh
./seaweed/run.sh
./redis/run.sh
