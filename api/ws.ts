import { createHeartbeatServer } from "../lib/ws-server.js";
import { getRedis } from "../lib/redis.js";

export default createHeartbeatServer({ redis: getRedis() });
