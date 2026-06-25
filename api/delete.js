import { proxyJson } from "./_proxy.js";

export default async function handler(req, res) {
  return proxyJson(req, res, "/delete");
}
