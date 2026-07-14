import { createHeartbeatServer } from "../lib/ws-server";
import { getRedis } from "../lib/redis";

export default createHeartbeatServer({ redis: getRedis() });
