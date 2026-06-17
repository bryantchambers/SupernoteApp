# SuperNote Web Sync & AI Processing Tool (AGENTS.md)

## 1. Project Overview
This project is a modern, responsive web application designed to interface with a SuperNote e-ink tablet's file system synced via OneDrive. The application provides a centralized dashboard to browse, archive, process, and edit SuperNote files (`.note`, images, etc.). 

A core feature of the application is an advanced AI processing pipeline that utilizes the `supernote-tool` library alongside Google's Gemini 2.5 Flash to convert handwritten notes into structured, highly formatted Markdown documents (including Mermaid charts and Obsidian-compatible image assets).

---

## 2. System Architecture & Tech Stack

* **Backend Framework:** Django (or FastAPI/Flask if requested by Orchestrator). Chosen for robust routing, built-in ORM for the archiving database, and ease of building REST/HTMX endpoints.
* **SuperNote Integration:** Pythonic `supernote-tool` (https://github.com/jya-dev/supernote-tool) for parsing, PDF conversion, image extraction, and programmatic text addition.
* **AI / Vision System:** Google GenAI Python SDK (`google-genai`). Model: `gemini-2.5-flash` for high-speed, multimodal image-to-text, OCR, and spatial layout reasoning.
* **Sync & Storage:**
    * **Cloud Sync:** `rclone` for syncing the local repository with OneDrive.
    * **File System Watcher:** Python's `watchdog` library (the pythonic, cross-platform equivalent to `fswatch`) running as a persistent background daemon/service to detect file system updates.
* **Database:** Local database (SQLite or PostgreSQL) to store file metadata, hashed file versions, archive paths, and processed Markdown text.
* **Frontend UI:** Responsive, mobile-and-desktop-friendly web interface (HTML/CSS/JS, potentially leveraging TailwindCSS and HTMX for a seamless SPA-like feel without a heavy frontend framework).

---

## 3. Strict Development Guardrails

All autonomous agents, tools, and human developers must strictly adhere to the following constraints during development:

1.  **Environment Confinement:** Development must *only* occur within the mamba environment named `SuperNoteTools` or the designated local development workspace folder.
2.  **No Unilateral Package Management:** Agents must **NOT** add packages, modify requirements files, or alter the core system independently. If a new dependency or system change is required, the agent must ask the **Orchestrator** to perform the addition.
3.  **Token Optimization:** Agents must utilize the `rtk` and `sqz` tools to reduce tokens and compress context wherever possible during communication and context sharing.
4.  **Configuration Phasing:** Hardcode the file system location to the local `Supernote` directory for the initial development phase. Ensure this is easily swappable to an App Settings configuration parameter for the production phase (which will utilize `rclone` for OneDrive).
5.  **No Code Generation in this File:** This document is strictly for architectural, behavioral, and rule-based guidance. 

---

## 4. Agent Roles & Responsibilities

If utilizing a multi-agent framework, responsibilities are divided as follows:

### 4.1 Orchestrator Agent
* **Role:** The sole authority for modifying the system environment, installing packages, and managing dependencies.
* **Tasks:** Approves dependency requests, manages the `SuperNoteTools` mamba environment, and coordinates task handoffs between other agents.

### 4.2 Backend & File System Agent
* **Role:** Manages the operating system interactions, file watchers, and database routing.
* **Tasks:**
    * Implement the `watchdog` continuous file-watching script.
    * Build the `rclone` wrapper for OneDrive synchronization.
    * Develop the database schema for archiving, utilizing content hashing to manage versioned backups.
    * Create the logic for the "Storage Destination" toggle (Device, Local Archive, Database, or combinations).
    * Implement bi-directional file movement (upload from app to SuperNote, pull from archive to SuperNote).

### 4.3 AI & Vision Processing Agent
* **Role:** Handles all interactions with `supernote-tool` and the Google GenAI API.
* **Tasks:**
    * Implement `.note` to PDF conversion (with embedded OCR).
    * Implement `.note` to image conversion for processing.
    * Develop the specific prompt engineering required for Gemini 2.5 Flash to:
        * Output strict Markdown.
        * Identify DAGs (Directed Acyclic Graphs) and workflows, rendering them as raw Mermaid.js syntax blocks.
        * Identify drawn tables and render them as standard Markdown tables.
        * Identify drawn charts/graphs, crop/save them as standard image assets, and embed them in the markdown using Obsidian syntax (e.g., `![[graph_asset_name.png]]`).
    * Implement the text-addition feature to write text back into `.note` files using `supernote-tool`.

### 4.4 Frontend & UI Agent
* **Role:** Builds the user interface and user experience.
* **Tasks:**
    * Create a modern file browser interface displaying specific SuperNote folders (Documents, Notes, etc.).
    * Implement a global search bar to locate files across the synced directory and local database.
    * Design a dedicated tab for the "Atelier" drawing app to aggregate, search, and convert artwork to PNG/SVG.
    * Build the interactive side-panel for file actions (Convert to PDF, AI Process to MD, Edit Note, Archive).

---

## 5. Core Workflows & Pipelines

### Pipeline A: The AI Note-to-Markdown Workflow
1.  User selects a `.note` file in the UI and clicks "Process with AI".
2.  Backend utilizes `supernote-tool` to render the `.note` layers into a high-quality temporary image.
3.  Backend authenticates with `google-genai` using the provided API key.
4.  The image and the engineered prompt are sent to `gemini-2.5-flash`.
5.  The response is parsed:
    * Standard handwriting is converted to Markdown text.
    * Mermaid blocks are verified for syntax.
    * Graph assets are saved locally, and their file paths are formatted to Obsidian `![[]]` standards.
6.  The final `.md` file is saved to the local database, made available for immediate download, and displayed in the UI.

### Pipeline B: Continuous Sync & Watch
1.  The `watchdog` daemon runs continuously on the server.
2.  It monitors the configured Supernote local directory for `on_created`, `on_modified`, or `on_deleted` events.
3.  When an event occurs, it logs the change to the database and updates the UI state (e.g., via WebSockets or polling) to reflect the new file structure.
4.  *(Production Phase)*: `rclone` triggers a sync protocol with OneDrive to ensure the local daemon has the latest files.

### Pipeline C: Atelier Art Aggregation
1.  System scans specifically for files generated by the SuperNote Atelier application.
2.  All identified files are pooled into a specific UI tab.
3.  Files are indexed by date and metadata for searchability.
4.  User can trigger a batch or single workflow to convert proprietary Atelier formats (or base image formats) into high-resolution PNGs or vector SVGs.



## 6. Description

I am building a tool for my SuperNote e-ink writing tablet. I would like to build a web app that operates off of the OneDrive synced folder system for the devices. That is the Supernote sync with OneDrive and in the synced Directory, I want to read the current files into a web app using that folder as the source.

I want the app to use the pythonic supernote package to be used to convert notes to pdfs or textfiles.

The main page will be an overview of the different folder systems on the Supernote. (Documents, Notes, etc). I would like system to read the files and allow me to interact with them via a web app file browser. Any file identified should then have options to be copied to a local database (with a hashed versioned back up or kept on the device). This could be a check box to decide where to store the file (either in the supernote directory or an archive backup directory (or database) or both. Files should be searchable from a top search bar.

once selected (maybe on the left side) the app should be able to do several things:
1. convert the note to a PDF with an embedded OCR'd text
- look into using the https://github.com/jya-dev/supernote-tool or similar if you discover something better
2. convert the note to an image and send the image to a cloud based VLM like gemini via an api key to likely a flash 2.5 model using the most up-todate (please research this) google python google genai library using a perfectly crafted prompt to:
a. convert the images to a markdown file:
i. include any figure or similar as a mermaid chart (if a DAG or other similar workflow like object
ii. include any tables or similar as a full table in the markdown
iii. if a part of the image looks like a graph, render the graph as a figure in the markdown file as an asset using similar structure to what Obsidian uses to store images
save this file as a MD in the database and make it downloadable
3. Pull the note and edit it or add text via an in window editor to the note using the text addition to the note in the python supernate-tool package.

Another features should be that the app has another tab that finds all image files from the artlier app on the supernote and has them in a single tab for easy identification make this searchable and make integrate a workflow to convert the notes to pngs or svgs

the app should use a linux equivalent of fswatch (https://emcrisostomo.github.io/fswatch/) to check when a sync occurs and files are updated. This will run on a server and always be on, always watching.

The app should also have a file upload so that document can be uploaded to the supernote storage, or pulled out of archive and passed back over.

The app will need to take a setting for the location of the file system. during development in the initial phase (tool building) the app will use a local copy of the `Supernote` directory. Late this will be passed as a configuration parameter in app settings and the app will use rclone (https://rclone.org/) to configure and sync to the actual system on OneDrive.

use a modern interface with a mobile and desktop interface. consider Django or other simpler systems.

## 7. Lessons Learned and Memories

## 8. Pending Tasks (Post-Phase 3)
*   **Quick Edit Implementation:** Complete the backend logic for `add_text_to_note` in `utils.py` and hook it up to the dashboard modal. Research if adding keywords/metadata is the most viable path for "writing" back to the device.