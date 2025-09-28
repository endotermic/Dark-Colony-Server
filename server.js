// server.js
import express from "express";
import http from "http";
import cors from "cors";
import helmet from "helmet";
import { Server } from "socket.io";
import RateLimiter from "socket.io-rate-limiter";

// ===== конфиг =====
const PORT = process.env.PORT || 3000;
// через запятую перечислите домены WP-страниц, где будет клиент
const ALLOWED_ORIGINS = (process.env.ALLOWED_ORIGINS || "https://ВАШ_ДОМЕН.wordpress.com")
  .split(",")
  .map(s => s.trim());

const MAX_ROOM_SIZE = parseInt(process.env.MAX_ROOM_SIZE || "8", 10);
const HEARTBEAT_INTERVAL_MS = 10_000; // пинг игроков
const ROOM_TTL_MS = 60 * 60 * 1000;   // авто-уборка пустых комнат (1ч)

// ===== http + io =====
const app = express();
app.use(helmet());
app.use(cors({ origin: (origin, cb) => {
  // WordPress.com может прислать null-оригин в предпросмотре — разрешим прямой заход
  if (!origin || ALLOWED_ORIGINS.includes(origin)) return cb(null, true);
  return cb(new Error("CORS: origin not allowed: " + origin));
}}));
app.get("/health", (_, res) => res.json({ ok: true, ts: Date.now() }));

const server = http.createServer(app);
const io = new Server(server, {
  transports: ["websocket", "polling"],
  cors: { origin: ALLOWED_ORIGINS },
});

// ===== простецкий матчмейкинг/комнаты =====
const roomsMeta = new Map(); // roomId -> { createdAt, lastActiveAt, started: bool }

function roomSize(roomId) {
  const r = io.sockets.adapter.rooms.get(roomId);
  return r ? r.size : 0;
}

io.use((socket, next) => {
  // лёгкая аутентификация через query токен/ник; для продакшена лучше JWT
  socket.data.name = (socket.handshake.auth?.name || "anon").toString().slice(0, 24);
  next();
});

// rate limit для защиты от спама событиями
const limiter = RateLimiter.create({
  interval: 1000,    // 1 сек окно
  maxInInterval: 30, // не более 30 событий/сек
  minDifference: 0
});

io.on("connection", (socket) => {
  // защита от спама
  socket.use(([event], next) => {
    if (limiter.throttle(socket.id)) return next(new Error("rate_limited"));
    next();
  });

  socket.on("join", ({ roomId }) => {
    if (typeof roomId !== "string" || !roomId) {
      socket.emit("error_msg", "invalid_room");
      return;
    }
    const size = roomSize(roomId);
    if (size >= MAX_ROOM_SIZE) {
      socket.emit("room_full", { roomId, max: MAX_ROOM_SIZE });
      return;
    }
    socket.join(roomId);
    const meta = roomsMeta.get(roomId) || { createdAt: Date.now(), lastActiveAt: Date.now(), started: false };
    roomsMeta.set(roomId, { ...meta, lastActiveAt: Date.now() });

    // сообщим всем в комнате
    io.to(roomId).emit("presence", {
      roomId,
      players: [...io.sockets.adapter.rooms.get(roomId)].map(id => ({ id, name: io.sockets.sockets.get(id)?.data.name }))
    });

    socket.emit("joined", { roomId, you: socket.id, name: socket.data.name, max: MAX_ROOM_SIZE });
  });

  // игровой апдейт — сервер просто ретранслирует (сервер-авторитет можно добавить позже)
  socket.on("state", ({ roomId, data }) => {
    if (!roomId) return;
    roomsMeta.set(roomId, { ...(roomsMeta.get(roomId) || {}), lastActiveAt: Date.now() });
    socket.to(roomId).emit("state", { id: socket.id, data });
  });

  // старт игры (по желанию — по готовности всех)
  socket.on("start", ({ roomId }) => {
    const meta = roomsMeta.get(roomId);
    if (!meta) return;
    roomsMeta.set(roomId, { ...meta, started: true, lastActiveAt: Date.now() });
    io.to(roomId).emit("started", { roomId, at: Date.now() });
  });

  socket.on("leave", ({ roomId }) => {
    socket.leave(roomId);
    io.to(roomId).emit("presence", {
      roomId,
      players: [...(io.sockets.adapter.rooms.get(roomId) || [])].map(id => ({ id, name: io.sockets.sockets.get(id)?.data.name }))
    });
  });

  socket.on("disconnect", () => {
    // обновим presence во всех комнатах, где был игрок
    for (const roomId of socket.rooms) {
      if (roomId === socket.id) continue;
      io.to(roomId).emit("presence", {
        roomId,
        players: [...(io.sockets.adapter.rooms.get(roomId) || [])].map(id => ({ id, name: io.sockets.sockets.get(id)?.data.name }))
      });
    }
  });
});

// ===== техобслуживание: пульс и уборка старых комнат =====
setInterval(() => {
  io.emit("ping", Date.now());
  const now = Date.now();
  for (const [roomId, meta] of roomsMeta) {
    if (roomSize(roomId) === 0 && now - meta.lastActiveAt > ROOM_TTL_MS) {
      roomsMeta.delete(roomId);
    }
  }
}, HEARTBEAT_INTERVAL_MS);

// ===== запуск =====
server.listen(PORT, () => {
  console.log(`Game host up on :${PORT}`);
  console.log("Allowed origins:", ALLOWED_ORIGINS);
});
