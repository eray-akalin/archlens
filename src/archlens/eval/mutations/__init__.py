"""Mutation registry (EVALUATION.md §2, §5): 13 defect mutations and 4 injection mutations, one
module each. Patch mutations (`generic=False`) apply `eval/patches/primary/<id>.patch`."""

from archlens.eval.mutation import Mutation
from archlens.eval.mutations import (
    i_comment,
    i_fakeevidence,
    i_readme,
    i_toolspoof,
    m_authoff,
    m_ciperms,
    m_cors,
    m_latest,
    m_logsecret,
    m_notests,
    m_notimeout,
    m_readme,
    m_root,
    m_secret,
    m_sqli,
    m_unpin,
    m_vulndep,
)

_MODULES = (
    m_secret, m_vulndep, m_sqli, m_authoff, m_cors, m_root, m_latest, m_notests, m_ciperms,
    m_unpin, m_logsecret, m_notimeout, m_readme, i_readme, i_toolspoof, i_comment,
    i_fakeevidence,
)  # fmt: skip
MUTATIONS: dict[str, Mutation] = {module.MUTATION.id: module.MUTATION for module in _MODULES}


def registry() -> dict[str, Mutation]:
    return dict(MUTATIONS)
