# Copied from Dark-Colony-Server (known to work on Fly.io). Changes: Node 22 (Node 20 is end-of-life),
# entry point via package.json "start".
FROM node:22-alpine

WORKDIR /app

# no runtime dependencies; kept for parity with the working image
COPY package*.json ./
RUN npm install --omit=dev

COPY . .

EXPOSE 8888

CMD ["npm", "start"]
