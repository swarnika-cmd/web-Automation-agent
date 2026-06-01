# Use the official Python slim image
FROM python:3.11-slim

# Prevent Python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium browser and its system dependencies
# This is crucial for cloud servers as it installs missing shared libraries (.so files)
RUN playwright install chromium
RUN playwright install-deps chromium

# Copy the rest of the application code
COPY . .

# Expose the dashboard port (Render overrides this with the PORT env, but Uvicorn binds correctly)
EXPOSE 8765

# Launch the FastAPI dashboard server as the entry point
CMD ["python", "dashboard.py"]
