from .activities import *
from .faq import *
from .users import *
from .workflows import *
from .logs import *
from .groups import *

class Hash:
    digest: str

    def __init__(self, digest: str):
        self.digest = digest

    def __repr__(self):
        return '<hashed string>'

    def __eq__(self, other):
        if isinstance(other, str):
            import crypto
            return crypto.check_password(self.digest, other)
        elif isinstance(other, Hash):
            return self.digest == other.digest
        return other is self

    def __ne__(self, other):
        return not self.__eq__(other)