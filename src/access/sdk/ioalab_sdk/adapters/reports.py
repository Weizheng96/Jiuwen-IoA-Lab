"""JSON view of the original verifier result; no alternative acceptance decision."""


def report(value):
    return {'status':value.status,'notBefore':value.not_before,'errors':value.errors,'checks':[run.outcome.checks for run in value.runs]}
