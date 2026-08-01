"""Wires the nodes in scai.graph.nodes into the LangGraph state graph (design.md Section 2)."""

from __future__ import annotations

from functools import partial

import psycopg
from langgraph.graph import END, START, StateGraph

from scai.config import Config
from scai.graph import nodes
from scai.graph.state import AgentState
from scai.providers.router import ProviderRouter


def build_graph(conn: psycopg.Connection, router: ProviderRouter, app_config: Config):
    graph = StateGraph(AgentState)

    graph.add_node("parse_intent", partial(nodes.parse_intent, router=router))
    graph.add_node("check_required_slots", nodes.check_required_slots)
    graph.add_node("ask_clarifying_question", nodes.ask_clarifying_question)
    graph.add_node(
        "retrieve_and_reason", partial(nodes.retrieve_and_reason, conn=conn, router=router, app_config=app_config)
    )
    graph.add_node("select_tools", nodes.select_tools)
    graph.add_node("call_tools", partial(nodes.call_tools, conn=conn))
    graph.add_node("synthesize_answer", partial(nodes.synthesize_answer, router=router))
    graph.add_node("render_output", nodes.render_output)

    graph.add_edge(START, "parse_intent")
    graph.add_edge("parse_intent", "check_required_slots")
    graph.add_conditional_edges(
        "check_required_slots",
        nodes.route_after_slot_check,
        {"ask_clarifying_question": "ask_clarifying_question", "retrieve_and_reason": "retrieve_and_reason"},
    )
    graph.add_edge("ask_clarifying_question", END)
    graph.add_edge("retrieve_and_reason", "select_tools")
    graph.add_edge("select_tools", "call_tools")
    graph.add_edge("call_tools", "synthesize_answer")
    graph.add_edge("synthesize_answer", "render_output")
    graph.add_edge("render_output", END)

    return graph.compile()
