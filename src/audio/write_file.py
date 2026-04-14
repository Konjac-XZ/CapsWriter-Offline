from subprocess import Popen
import wave 
import numpy as np
from typing import BinaryIO, Union, cast



def write_file(file: Union[Popen, wave.Wave_write], data: np.ndarray):
    if isinstance(file, Popen):
        if file.stdin is not None:
            stdin = cast(BinaryIO, file.stdin)
            stdin.write(data.tobytes())
            stdin.flush()
    elif isinstance(file, wave.Wave_write):
        data = (data * (2**15 - 1)).astype(np.int16).tobytes()
        file.writeframes(data)
