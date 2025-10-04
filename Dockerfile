# Simple Dockerfile for Fly.io deployment
FROM node:20-alpine

# Create app directory
WORKDIR /app

# Install dependencies (none besides dev) - copy only package files first for caching
COPY package*.json ./
RUN npm install --omit=dev

# Copy source
COPY . .

# Expose the TCP port
EXPOSE 8888

# Fly will provide PORT env var; fallback kept in code.
CMD ["npm", "start"]
