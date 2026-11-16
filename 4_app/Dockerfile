# Start from an official image that already has Python
FROM python:3.12-slim

# Every command after this runs inside /app in the image
WORKDIR /app

# Copy the package list first and install it
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Then copy your code
COPY streamlit_app.py .

# What the container runs when it starts
CMD ["streamlit", "run", "streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501"]