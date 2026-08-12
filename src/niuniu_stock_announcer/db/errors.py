"""v2 持久化边界的稳定业务异常。"""


class PersistenceError(RuntimeError):
    """持久化边界无法完成业务操作。"""


class PersistenceConflictError(PersistenceError):
    """同一业务身份已存在不一致的不可变快照。"""


class RecordNotFoundError(PersistenceError):
    """请求的持久化记录不存在。"""


class InvalidStateTransitionError(PersistenceError):
    """记录当前状态不允许请求的状态转换。"""
