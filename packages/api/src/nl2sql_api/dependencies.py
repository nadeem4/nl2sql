from fastapi import Request

from nl2sql import NL2SQL
from nl2sql_api.jobs import Jobs


def get_engine(request: Request) -> NL2SQL:
    return request.app.state.engine


def get_jobs(request: Request) -> Jobs:
    return request.app.state.jobs
