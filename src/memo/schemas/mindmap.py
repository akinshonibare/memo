"""The map of one thing (ADR 0049): a person, a note, an idea or a prayer in the
middle, and what memo has linked to it around it."""

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel

from memo.schemas.faith import PassageOut

NodeKind = Literal["person", "note", "idea", "prayer", "passage", "todo", "event"]
CenterKind = Literal["person", "note", "idea", "prayer"]


class MapNode(BaseModel):
	kind: NodeKind
	# A uuid for everything but a passage, whose id is its reference ("Romans 8").
	id: str
	label: str
	detail: str | None = None
	when: dt.date | None = None
	# The note to open, for anything that came from one.
	capture_id: uuid.UUID | None = None
	passage: PassageOut | None = None
	# Whether this node can be put in the middle (person, note, idea, prayer).
	centrable: bool = False


class MapLink(BaseModel):
	node: MapNode
	# How it connects to the middle, in words: "about", "prayer for", "related".
	relation: str


class MapOut(BaseModel):
	center: MapNode
	links: list[MapLink]
