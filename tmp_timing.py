import time

from quarry.common.timing import StageTimer

t = StageTimer()
with t.stage("sleep"):
    time.sleep(0.05)
print(t.as_dict())