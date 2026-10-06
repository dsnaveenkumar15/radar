"""A small question-answering service over a folder of notes, written for LangChain 0.3 (an older release on purpose)."""
from langchain.agents import AgentExecutor, AgentType, initialize_agent
from langchain.memory import ConversationBufferMemory
from langchain.tools import Tool

from retriever import search_notes

memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)


def build_agent(llm):
    notes_tool = Tool(name="notes", func=search_notes, description="Search the team's notes for a phrase")
    agent: AgentExecutor = initialize_agent(
        [notes_tool], llm, agent=AgentType.CONVERSATIONAL_REACT_DESCRIPTION, memory=memory, verbose=False)
    return agent


def answer(agent, question: str) -> str:
    return agent.run(question)
