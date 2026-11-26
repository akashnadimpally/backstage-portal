# ${{ values.name }}

${{ values.description }}

## Overview

This repository was scaffolded using the Backstage Python Application Template.

## Prerequisites

- Python ${{ values.pythonVersion }}
- pip

## Getting Started

### Create a virtual environment

```bash
python -m venv .venv
```

### Activate the virtual environment

On Linux/macOS:

```bash
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

### Install dependencies

```bash
pip install -r requirements.txt
```

### Run the application

```bash
python app.py
```

## Running tests

```bash
pytest
```
