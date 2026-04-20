# Shopy Colab Final Package

Run in Google Colab:

```python
%cd /content
!git clone <your-repo-url>
%cd /content/<repo-name>/colab
!pip install -r requirements.txt
!python main.py
```

If you want DB-only bootstrap first:

```python
import sys
sys.path.insert(0, './colab')
from db import bootstrap_database_only
bootstrap_database_only()
```
