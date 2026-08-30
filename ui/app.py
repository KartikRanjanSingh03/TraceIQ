"""
ui/app.py — Entry-point shim for: python -m streamlit run ui/app.py

The canonical Streamlit implementation lives in src/ui/app.py.
This shim delegates execution there so the launch command above works
without duplicating any UI logic.
"""
import runpy
import os

_src_app = os.path.join(os.path.dirname(__file__), "..", "src", "ui", "app.py")
runpy.run_path(os.path.normpath(_src_app), run_name="__main__")
