#!/bin/bash

# Load environment variables from .env
if [ -f .env ]; then
  export $(cat .env | grep -v '#' | xargs)
fi

# Default port if not set
SERVER_PORT=${PORT:-8000}

echo "Starting SuperNote Web Sync on port $SERVER_PORT..."
mamba run -n SuperNoteTools python manage.py runserver 0.0.0.0:$SERVER_PORT
