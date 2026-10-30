"""Public errors shared by platform domain boundaries."""


class LabError(Exception):
    def __init__(self, code, message, *, status=400, recovery='correct_input'):
        super().__init__(message)
        self.code, self.status, self.recovery = code, status, recovery

    def public(self):
        return {'code':self.code,'message':str(self),'recovery':self.recovery}
