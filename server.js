// server.js
import express from "express";
import http from "http";
import cors from "cors";
import helmet from "helmet";
import { Server } from "socket.io";
import { RateLimiterMemory } from "rate-limiter-flexible";

// ===== конфиг =====
const PORT = process.env.PORT || 3000;
// перечислите домены WP-страниц (через запятую) — протокол обязателен
const ALLOWED_ORIGINS = (process.env.ALLOWED_ORIGINS || "https://endotermic.wordpress.com")
  .split(",")
  .map(s => s.trim())
  .filter(Boolean);

const MAX_ROOM_SIZE = parseInt(process.env.MAX_ROOM_SIZE || "8", 10);
const HEARTBEAT_INTERVAL_MS = 10_000; // пинг игроков
const ROOM_TTL_MS = 60 * 60 * 1000;   // авто-уборка пустых комнат (1ч)

// ===== http =====
const app = express();
app.disable("x-powered-by");
app.use(helmet());
app.use(cors({
  origin: (origin, cb) => {
    // На предпросмотрах WordPress origin может быть null — разрешим такой заход.
    if (!origin || ALLOWED_ORIGINS.includes(origin)) return cb(null, true);
    return cb(new Error("CORS: origin not allowed: " + origin));
  }
}));
app.get("/", (_, res) => res.type("text/plain").send("game-host up"));
app.get("/health", (_, res) => res.json({ ok: true, ts: Date.now() }));

const server = http.createServer(app);

// ===== socket.io =====
const io = new Server(server, {
  transports: ["websocket", "polling"],
  // Жёсткий лимит на размер сообщения (~100 КБ)
  maxHttpBufferSize: 1e5,
  // Проверка origin для апгрейда соединения
  allowRequest: (req, callback) => {
    const origin = req.headers.origin || "";
    if (!origin || ALLOWED_ORIGINS.includes(origin)) return callback(null, true);
    return callback("CORS: origin not allowed: " + origin, false);
  }
});

// ===== rate limit событий: 30 событий/сек на сокет =====
const eventsLimiter = new RateLimiterMemory({
  points: 30,
  duration: 1
});

// ===== простые комнаты =====
const roomsMeta = new Map(); // roomId -> { createdAt, lastActiveAt, started: bool }

function roomSize(roomId) {
  const set = io.sockets.adapter.rooms.get(roomId);
  return set ? set.size : 0;
}
function listPlayers(roomId) {
  const set = io.sockets.adapter.rooms.get(roomId);
  if (!set) return [];
  return [...set].map(id => {
    const s = io.sockets.sockets.get(id);
    return { id, name: s?.data?.name || id };
  });
}

io.use((socket, next) => {
  // Лёгкая «авторизация» именем. Для боевого режима — JWT.
  const raw = socket.handshake.auth?.name ?? "anon";
  socket.data.name = String(raw).slice(0, 24);
  next();
});

io.on("connection", (socket) => {
  // лимитер на каждое входящее событие
  socket.use(async (_packet, next) => {
    try { await eventsLimiter.consume(socket.id); next(); }
    catch { next(new Error("rate_limited")); }
  });

  // сообщим клиенту о rate limit явным событием
  socket.on("error", (err) => {
    if (err && err.message === "rate_limited") socket.emit("error_msg", "rate_limited");
  });

  socket.on("join", ({ roomId }) => {
    if (typeof roomId !== "string" || roomId.length < 1 || roomId.length > 64) {
      socket.emit("error_msg", "invalid_room");
      return;
    }

    if (roomSize(roomId) >= MAX_ROOM_SIZE) {
      socket.emit("room_full", { roomId, max: MAX_ROOM_SIZE });
      return;
    }

    socket.join(roomId);
    const meta = roomsMeta.get(roomId) || { createdAt: Date.now(), lastActiveAt: Date.now(), started: false };
    roomsMeta.set(roomId, { ...meta, lastActiveAt: Date.now() });

    io.to(roomId).emit("presence", { roomId, players: listPlayers(roomId) });
    socket.emit("joined", { roomId, you: socket.id, name: socket.data.name, max: MAX_ROOM_SIZE });
  });

  // ретрансляция игрового состояния
  socket.on("state", ({ roomId, data }) => {
    if (typeof roomId !== "string") return;
    roomsMeta.set(roomId, { ...(roomsMeta.get(roomId) || {}), lastActiveAt: Date.now() });
    // по желанию: валидируйте/ограничивайте «data»
    socket.to(roomId).emit("state", { id: socket.id, data });
  });

  socket.on("start", ({ roomId }) => {
    if (typeof roomId !== "string") return;
    const meta = roomsMeta.get(roomId) || { createdAt: Date.now(), lastActiveAt: Date.now(), started: false };
    roomsMeta.set(roomId, { ...meta, started: true, lastActiveAt: Date.now() });
    io.to(roomId).emit("started", { roomId, at: Date.now() });
  });

  socket.on("leave", ({ roomId }) => {
    if (typeof roomId !== "string") return;
    socket.leave(roomId);
    io.to(roomId).emit("presence", { roomId, players: listPlayers(roomId) });
  });

  // на стадии "disconnecting" комнаты ещё известны
  socket.on("disconnecting", () => {
    for (const roomId of socket.rooms) {
      if (roomId === socket.id) continue;
      io.to(roomId).emit("presence", { roomId, players: listPlayers(roomId) });
    }
  });
});

// ===== служебные таймеры =====
setInterval(() => {
  io.emit("ping", Date.now());
  const now = Date.now();
  for (const [roomId, meta] of roomsMeta) {
    if (roomSize(roomId) === 0 && now - (meta.lastActiveAt || meta.createdAt) > ROOM_TTL_MS) {
      roomsMeta.delete(roomId);
    }
  }
}, HEARTBEAT_INTERVAL_MS);

// ===== старт =====
server.listen(PORT, () => {
  console.log(`Game host listening on :${PORT}`);
  console.log("Allowed origins:", ALLOWED_ORIGINS.join(", "));
});
