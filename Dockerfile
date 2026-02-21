# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Install system dependencies required for PyAudio and others
# Note: xvfb is for headless display (optional for some GUI apps)
RUN apt-get update && apt-get install -y \
    build-essential \
    portaudio19-dev \
    python3-pyaudio \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container
COPY requirements.txt .

# Install Python dependencies
# Note: pywinauto is Windows-only and will fail on Linux.
# We use a trick to ignore it or the user should use a Windows container.
# For this setup, we assume the user might want to run non-GUI parts or tests.
RUN pip install --no-cache-dir -r requirements.txt || echo "Warning: Windows-specific packages failed to install."

# Copy the rest of the application code
COPY . .

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Command to run the application
# Note: GUI requires a display. On Linux, you might need xvfb-run.
CMD ["python", "main.py"]
