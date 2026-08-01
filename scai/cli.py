"""Interactive REPL for the supply chain agent — no frontend in v1 (design.md Section 9)."""

from __future__ import annotations

from scai import db
from scai.config import load_config
from scai.graph.build import build_graph
from scai.providers.router import ProviderRouter


def main() -> None:
    config = load_config()
    router = ProviderRouter(config)

    with db.connect(config) as conn:
        session_id = db.create_session(conn, user_id="cli-user")
        graph = build_graph(conn, router, config)
        slots: dict = {}

        print("Supply Chain Agent (Ctrl+C to exit)")
        while True:
            try:
                user_message = input("\nyou> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not user_message:
                continue

            db.append_message(conn, session_id, "user", user_message, slots)
            result = graph.invoke({"session_id": session_id, "user_message": user_message, "slots": slots})
            slots = result.get("slots", slots)
            answer = result.get("answer", "(no answer produced)")
            db.append_message(conn, session_id, "assistant", answer, slots)
            print(f"\nagent> {answer}")


if __name__ == "__main__":
    main()
