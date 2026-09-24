"""
WHY THIS EXISTS
The "librarian". It reads your documents from the knowledge/ folder, cuts
them into short overlapping passages, and finds the passages that best match
a sentence or a question, so every alert and every spoken answer can point
at the exact document text it relies on.

FAILURE IT PREVENTS
Alerts and answers that are not grounded in your documents (the bot making
things up), and the bot quoting a document you have since deleted or edited
(the index is rebuilt whenever a file in the folder changes).

Owner: lane-engine.
"""
from .index import KnowledgeBase, Passage

__all__ = ["KnowledgeBase", "Passage"]
