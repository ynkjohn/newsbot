const express = require("express");
const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
} = require("@whiskeysockets/baileys");
const { Boom } = require("@hapi/boom");
const pino = require("pino");
const qrcode = require("qrcode-terminal");

const app = express();
app.use(express.json());

const PORT = parseInt(process.env.PORT || "3000", 10);
const WEBHOOK_URL =
  process.env.WEBHOOK_URL || "http://newsbot:8000/webhook/whatsapp";
const SESSION_DIR = process.env.SESSION_DIR || "./auth_session";
/** Same token as newsbot WHATSAPP_BRIDGE_TOKEN: sent on webhook + required on POST /send when set */
const WHATSAPP_BRIDGE_TOKEN = (process.env.WHATSAPP_BRIDGE_TOKEN || "").trim();

if (!WHATSAPP_BRIDGE_TOKEN) {
  throw new Error("WHATSAPP_BRIDGE_TOKEN is required");
}

const logger = pino({ level: process.env.LOG_LEVEL || "silent" });

let sock = null;
let latestQR = null;
let connectionStatus = "disconnected";

function normalizeJid(jid = "") {
  return String(jid).trim().replace(/:\d+(?=@)/, "");
}

function extractIdentityNumber(jid = "") {
  const match = normalizeJid(jid).match(/^(\d+)/);
  return match ? match[1] : "";
}

function requireAuth(req, res, next) {
  if (req.headers.authorization !== `Bearer ${WHATSAPP_BRIDGE_TOKEN}`) {
    return res.status(401).json({ error: "Unauthorized" });
  }
  next();
}

async function startWhatsApp() {
  const { version } = await fetchLatestBaileysVersion();
  const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);

  sock = makeWASocket({
    version,
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, logger),
    },
    printQRInTerminal: false,
    logger,
    defaultQueryTimeoutMs: 60000,
    connectTimeoutMs: 30000,
  });

  sock.ev.on("connection.update", async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      latestQR = qr;
      connectionStatus = "qr_ready";
      console.log("\n=== ESCANEIE O QR CODE ABAIXO COM O WHATSAPP ===");
      qrcode.generate(qr, { small: true });
      console.log("================================================\n");
    }

    if (connection === "close") {
      const statusCode = new Boom(lastDisconnect?.error)?.output?.statusCode;
      const shouldReconnect =
        statusCode !== DisconnectReason.loggedOut &&
        statusCode !== DisconnectReason.connectionClosed;
      console.log(
        `Conexao encerrada. Status: ${statusCode}. Reconectando: ${shouldReconnect}`,
      );
      connectionStatus = "disconnected";
      latestQR = null;
      if (shouldReconnect) setTimeout(() => startWhatsApp(), 3000);
    } else if (connection === "open") {
      connectionStatus = "connected";
      latestQR = null;
      console.log("WhatsApp conectado com sucesso!");
    }
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    if (type !== "notify") return;
    for (const msg of messages) {
      if (msg.key.remoteJid === "status@broadcast") continue;
      if (msg.key.fromMe) continue;

      const conversation =
        msg.message?.conversation ||
        msg.message?.extendedTextMessage?.text ||
        "";

      if (!conversation || !msg.key.remoteJid) continue;

      // DEBUG: Log all incoming messages
      console.log(
        `[INCOMING] JID: ${msg.key.remoteJid}, isGroup: ${msg.key.remoteJid.endsWith("@g.us")}, text: ${conversation.slice(0, 50)}`,
      );

      // Check if message mentions the bot (for groups)
      const isGroup = msg.key.remoteJid.endsWith("@g.us");
      if (isGroup) {
        const mentions =
          msg.message?.extendedTextMessage?.contextInfo?.mentionedJid || [];
        const msgText =
          msg.message?.extendedTextMessage?.text ||
          msg.message?.conversation ||
          "";

        // Get bot info - extract phone number from bot JID
        const botJid = sock.user?.id || "";
        const botLid = sock.user?.lid || "";
        // Extract phone number from bot JID (could be: 5514996083583:23@s.whatsapp.net or 5514996083583@s.whatsapp.net)
        const botPhoneMatch = botJid.match(/^(\d+)/);
        const botPhone = botPhoneMatch ? botPhoneMatch[1] : "";
        const botLidNumber = extractIdentityNumber(botLid);

        // Check if bot is specifically mentioned (in mentions array)
        let isBotMentioned = false;
        const botIdentitySet = new Set(
          [
            botJid,
            botLid,
            botPhone ? `${botPhone}@s.whatsapp.net` : "",
            botPhone ? `${botPhone}@lid` : "",
          ]
            .map(normalizeJid)
            .filter(Boolean),
        );

        isBotMentioned = mentions.some((mention) => {
          const normalizedMention = normalizeJid(mention);
          const mentionNumber = extractIdentityNumber(mention);

          return (
            botIdentitySet.has(normalizedMention) ||
            mentionNumber === botPhone ||
            mentionNumber === botLidNumber
          );
        });

        console.log(
          `[DEBUG] isGroup: true, botJid: ${botJid}, botLid: ${botLid}, botPhone: ${botPhone}, mentions: ${JSON.stringify(mentions)}, isBotMentioned: ${isBotMentioned}, msgText: "${msgText.slice(0, 50)}"`,
        );

        // IN GROUPS: Only process if the bot is explicitly mentioned.
        if (!isBotMentioned) {
          console.log(
            `[IGNORED] Grupo - sem menção ao bot: "${msgText.slice(0, 50)}"`,
          );
          continue;
        }

        console.log(
          `[PROCESSING] Grupo - mencionado: "${msgText.slice(0, 50)}"`,
        );
      }

      const payload = {
        key: {
          remoteJid: msg.key.remoteJid,
          fromMe: msg.key.fromMe,
          id: msg.key.id,
        },
        message: { conversation },
        pushName: msg.pushName || "",
        messageType: "conversation",
      };

      try {
        const headers = { "Content-Type": "application/json" };
        if (WHATSAPP_BRIDGE_TOKEN) {
          headers["Authorization"] = `Bearer ${WHATSAPP_BRIDGE_TOKEN}`;
        }
        const resp = await fetch(WEBHOOK_URL, {
          method: "POST",
          headers,
          body: JSON.stringify(payload),
        });
        console.log(`Webhook enviado -> status ${resp.status}`);
        // #region agent log
        fetch("http://127.0.0.1:7797/ingest/1c7cbab7-0325-47ac-bf7a-e14b3081771f", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Debug-Session-Id": "8222de",
          },
          body: JSON.stringify({
            sessionId: "8222de",
            location: "whatsapp-bridge/index.js:webhook_response",
            message: "webhook_post_result",
            hypothesisId: "H5",
            data: {
              willSendAuth: Boolean(WHATSAPP_BRIDGE_TOKEN),
              responseStatus: resp.status,
            },
            timestamp: Date.now(),
            runId: "pre",
          }),
        }).catch(() => {});
        // #endregion
      } catch (err) {
        console.error(`Erro no webhook: ${err.message}`);
      }
    }
  });
}

app.post("/send", requireAuth, async (req, res) => {
  const { number, text } = req.body || {};
  if (!number || !text)
    return res
      .status(400)
      .json({ error: "Campos 'number' e 'text' sao obrigatorios" });
  if (!sock || connectionStatus !== "connected")
    return res
      .status(503)
      .json({ error: "WhatsApp nao conectado", status: connectionStatus });

  const jid = number.includes("@") ? number : `${number}@s.whatsapp.net`;
  try {
    const sent = await sock.sendMessage(jid, { text });
    return res.json({ success: true, messageId: sent.key.id });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
});

app.get("/qrcode", requireAuth, (req, res) => {
  if (!latestQR)
    return res
      .status(404)
      .json({ error: "Nenhum QR disponivel", status: connectionStatus });
  const QRCode = require("qrcode");
  QRCode.toDataURL(latestQR, (err, url) => {
    if (err) return res.status(500).json({ error: "Erro ao gerar imagem QR" });
    res.json({ qr: url, status: connectionStatus });
  });
});

app.get("/status", requireAuth, (req, res) =>
  res.json({ status: connectionStatus }),
);

app.listen(PORT, "0.0.0.0", () => {
  console.log(`whatsapp-bridge rodando na porta ${PORT}`);
  startWhatsApp().catch((err) => {
    console.error(`Falha ao iniciar WhatsApp: ${err.message}`);
    process.exit(1);
  });
});
