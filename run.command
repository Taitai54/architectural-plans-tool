#!/bin/bash
# Double-click this file on a Mac to start the Architectural Plans Tool.
cd "$(dirname "$0")" || exit 1
./run.sh
echo ""
read -n 1 -s -r -p "The tool has stopped. Press any key to close this window."
