import sys

from .main import main

try:
    sys.exit(main())
except KeyboardInterrupt:
    sys.exit(0)
