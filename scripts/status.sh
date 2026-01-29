#!/bin/bash
ping -c 1 -W 1 "$1" > /dev/null && echo "ON" || echo "OFF"
