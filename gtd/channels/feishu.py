import asyncio
import json
import logging
import threading

from lark_oapi.api.im.v1.model.create_message_request import CreateMessageRequest
from lark_oapi.api.im.v1.model.create_message_request_body import \
    CreateMessageRequestBody
from lark_oapi.api.im.v1.resource.message import Message
from lark_oapi.core.model import Config
from lark_oapi.event.dispatcher_handler import (
    EventDispatcherHandlerBuilder,
    P2ImMessageReceiveV1,
)
from lark_oapi.ws import Client as WsClient

from gtd.db import add_to_inbox
from gtd.engine.classify import classify
from gtd.settings import settings

logger = logging.getLogger(__name__)


def _on_message(event: P2ImMessageReceiveV1) -> None:
    msg = event.event.message
    if not msg or msg.message_type != "text":
        return

    try:
        content = json.loads(msg.content)
        text = content.get("text", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return

    if not text:
        return

    sender_id = None
    if event.event.sender and event.event.sender.sender_id:
        sender_id = event.event.sender.sender_id.open_id

    meta = json.dumps(
        {"sender_open_id": sender_id, "chat_type": msg.chat_type},
        ensure_ascii=False,
    )

    item_id = add_to_inbox(text, source="feishu", source_meta=meta)
    _send_reply(msg.chat_id, "已收集 ✓")
    threading.Thread(
        target=classify, args=(item_id,), daemon=True, name=f"classify-{item_id}"
    ).start()


def _send_reply(chat_id: str, text: str) -> None:
    config = Config()
    config.app_id = settings.feishu_app_id
    config.app_secret = settings.feishu_app_secret

    body = (
        CreateMessageRequestBody.builder()
        .receive_id(chat_id)
        .msg_type("text")
        .content(json.dumps({"text": text}))
        .build()
    )
    req = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(body)
        .build()
    )

    try:
        Message(config).create(req)
    except Exception:
        logger.exception("Failed to send Feishu reply")


def _run_ws_client(client: WsClient) -> None:
    """Run WS client in a fresh event loop, isolated from FastAPI's loop."""
    import lark_oapi.ws.client as ws_client_module

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    ws_client_module.loop = loop  # override the module-level loop captured at import
    try:
        client.start()
    except Exception:
        logger.exception("Feishu WS client stopped unexpectedly")


def start_feishu():
    """Start Feishu WebSocket listener in a daemon thread."""
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        logger.info("Feishu credentials not set, skipping bot startup")
        return

    handler = (
        EventDispatcherHandlerBuilder(encrypt_key="", verification_token="")
        .register_p2_im_message_receive_v1(_on_message)
        .build()
    )

    client = WsClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        event_handler=handler,
    )

    t = threading.Thread(
        target=_run_ws_client, args=(client,), daemon=True, name="feishu-ws"
    )
    t.start()
    logger.info("Feishu WebSocket listener started")
