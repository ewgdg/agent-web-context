import os
import mimetypes
from pathlib import Path
from typing import List
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from urllib.parse import quote, unquote

router = APIRouter(prefix="/logs", tags=["logs"])

LOGS_DIR = Path("./logs")

# Ensure logs directory exists on import
LOGS_DIR.mkdir(exist_ok=True)


def get_file_browser_html(current_path: Path, request_url: str) -> str:
    """Generate HTML file browser with right-click delete functionality."""

    # Calculate relative path from logs root
    try:
        rel_path = current_path.relative_to(LOGS_DIR)
        breadcrumb = str(rel_path) if str(rel_path) != "." else ""
    except ValueError:
        breadcrumb = ""

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Log Files Browser - {breadcrumb or "Root"}</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            .header {{ margin-bottom: 20px; }}
            .breadcrumb {{ color: #666; margin-bottom: 10px; }}
            .controls {{ margin-bottom: 20px; }}
            .delete-all-btn {{ 
                background: #dc3545; color: white; padding: 8px 16px; 
                border: none; border-radius: 4px; cursor: pointer; 
            }}
            .delete-all-btn:hover {{ background: #c82333; }}
            .file-list {{ list-style: none; padding: 0; }}
            .file-item {{ 
                padding: 8px; margin: 2px 0; border-radius: 4px; 
                cursor: pointer; display: flex; justify-content: space-between;
                align-items: center;
            }}
            .file-item:hover {{ background: #f8f9fa; }}
            .file-link {{ text-decoration: none; color: #007bff; flex: 1; }}
            .file-link:hover {{ text-decoration: underline; }}
            .folder-link {{ color: #6f42c1; font-weight: bold; }}
            .file-size {{ color: #666; font-size: 0.9em; margin-left: 10px; }}
            .context-menu {{
                display: none; position: absolute; background: white;
                border: 1px solid #ccc; border-radius: 4px; padding: 5px 0;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1); z-index: 1000;
            }}
            .context-menu-item {{
                padding: 8px 15px; cursor: pointer; color: #dc3545;
            }}
            .context-menu-item:hover {{ background: #f8f9fa; }}
            .selected {{ background: #e3f2fd !important; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Log Files Browser</h1>
            <div class="breadcrumb">Path: /{breadcrumb}</div>
        </div>
        
        <div class="controls">
            <button class="delete-all-btn" onclick="deleteAllItems()">Delete All</button>
        </div>
        
        <ul class="file-list" id="fileList">
    """

    # Add parent directory link if not at root
    if current_path != LOGS_DIR:
        parent_path = current_path.parent
        parent_rel = (
            parent_path.relative_to(LOGS_DIR) if parent_path != LOGS_DIR else ""
        )
        parent_url = f"/logs{'/' + str(parent_rel) if parent_rel else ''}"
        html += f"""
            <li class="file-item">
                <a href="{parent_url}" class="file-link folder-link">📁 ..</a>
            </li>
        """

    if current_path.exists():
        items = sorted(
            current_path.iterdir(), key=lambda x: (x.is_file(), x.name.lower())
        )

        for item in items:
            item_rel = item.relative_to(LOGS_DIR)
            if item.is_dir():
                item_url = f"/logs/{item_rel}"
                html += f"""
                    <li class="file-item" data-filename="{item.name}" data-filepath="{item_rel}" data-type="folder">
                        <a href="{item_url}" class="file-link folder-link">📁 {item.name}</a>
                    </li>
                """
            else:
                file_size = item.stat().st_size
                size_str = format_file_size(file_size)
                item_url = f"/logs/{item_rel}"

                # Use different icons for different file types
                icon = (
                    "🖼️"
                    if item.suffix.lower()
                    in [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"]
                    else "📄"
                )

                html += f"""
                    <li class="file-item" data-filename="{item.name}" data-filepath="{item_rel}">
                        <a href="{item_url}" class="file-link">{icon} {item.name}</a>
                        <span class="file-size">{size_str}</span>
                    </li>
                """

    html += """
        </ul>
        
        <div class="context-menu" id="contextMenu">
            <div class="context-menu-item" onclick="deleteSelectedItem()">Delete</div>
        </div>
        
        <script>
            let selectedFile = null;
            
            // Right-click context menu
            document.addEventListener('contextmenu', function(e) {
                const fileItem = e.target.closest('.file-item[data-filename]');
                if (fileItem) {
                    e.preventDefault();
                    selectedFile = fileItem;
                    
                    // Clear previous selection
                    document.querySelectorAll('.file-item').forEach(item => {
                        item.classList.remove('selected');
                    });
                    fileItem.classList.add('selected');
                    
                    const contextMenu = document.getElementById('contextMenu');
                    contextMenu.style.display = 'block';
                    contextMenu.style.left = e.pageX + 'px';
                    contextMenu.style.top = e.pageY + 'px';
                }
            });
            
            // Hide context menu on click elsewhere
            document.addEventListener('click', function() {
                document.getElementById('contextMenu').style.display = 'none';
                document.querySelectorAll('.file-item').forEach(item => {
                    item.classList.remove('selected');
                });
            });
            
            async function deleteSelectedItem() {
                if (!selectedFile) return;
                
                const filepath = selectedFile.dataset.filepath;
                const displayName = selectedFile.dataset.filename;
                const isFolder = selectedFile.dataset.type === 'folder';
                const itemType = isFolder ? 'folder' : 'file';
                
                if (confirm(`Delete ${itemType} "${displayName}"?${isFolder ? ' This will delete all contents!' : ''}`)) {
                    try {
                        const endpoint = isFolder ? `/logs/delete-folder/${encodeURIComponent(filepath)}` : `/logs/delete/${encodeURIComponent(filepath)}`;
                        const response = await fetch(endpoint, {
                            method: 'DELETE'
                        });
                        
                        if (response.ok) {
                            selectedFile.remove();
                            alert(`${itemType.charAt(0).toUpperCase() + itemType.slice(1)} deleted successfully`);
                        } else {
                            const error = await response.json();
                            alert(`Error deleting ${itemType}: ` + error.detail);
                        }
                    } catch (error) {
                        alert(`Error deleting ${itemType}: ` + error.message);
                    }
                }
                
                document.getElementById('contextMenu').style.display = 'none';
            }
            
            async function deleteAllItems() {
                if (confirm('Delete ALL files and folders in current directory? This cannot be undone!')) {
                    try {
                        const response = await fetch('/logs/delete-all', {
                            method: 'DELETE',
                            headers: {
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify({path: window.location.pathname})
                        });
                        
                        if (response.ok) {
                            location.reload();
                        } else {
                            const error = await response.json();
                            alert('Error deleting files: ' + error.detail);
                        }
                    } catch (error) {
                        alert('Error deleting files: ' + error.message);
                    }
                }
            }
        </script>
    </body>
    </html>
    """

    return html


def format_file_size(size_bytes: float) -> str:
    """Format file size in human readable format."""
    if size_bytes == 0:
        return "0 B"

    size_names = ["B", "KB", "MB", "GB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1

    return f"{size_bytes:.1f} {size_names[i]}"


@router.get("", include_in_schema=False)
@router.get("/", include_in_schema=False)
@router.get("/{path:path}", include_in_schema=False)
async def browse_logs(request: Request, path: str = ""):
    """Browse log files and directories."""
    current_path = LOGS_DIR / path if path else LOGS_DIR

    # Security check
    try:
        current_path.resolve().relative_to(LOGS_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

    # Don't create directories - just return 404 if path doesn't exist
    if not current_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    # If it's a file, serve it directly
    if current_path.is_file():
        mime_type, _ = mimetypes.guess_type(str(current_path))
        if mime_type is None:
            mime_type = "application/octet-stream"

        # Check if download is explicitly requested
        download = request.query_params.get("download") == "1"

        # Define truly binary file types that should always be downloaded
        download_only_extensions = {
            ".zip",
            ".pdf",
            ".exe",
            ".bin",
            ".gz",
            ".tar",
            ".bz2",
            ".xz",
            ".mp4",
            ".avi",
            ".mp3",
            ".wav",
            ".woff",
            ".woff2",
            ".ttf",
            ".otf",
        }
        download_only_mime_prefixes = (
            "video/",
            "audio/",
            "application/zip",
            "application/x-",
            "application/gzip",
            "application/pdf",
        )

        # Check if it's a download-only file
        is_download_only = (
            current_path.suffix.lower() in download_only_extensions
            or any(
                mime_type.startswith(prefix) for prefix in download_only_mime_prefixes
            )
        )

        # For images, display with HTML wrapper
        if mime_type.startswith("image/") and not download:
            icon = "🖼️"
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>{current_path.name}</title>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 20px; background: #f8f9fa; }}
                    .header {{ background: white; padding: 15px; border-radius: 5px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
                    .header h1 {{ margin: 0; color: #333; }}
                    .back-link {{ color: #007bff; text-decoration: none; }}
                    .back-link:hover {{ text-decoration: underline; }}
                    .image-container {{ 
                        background: white; padding: 20px; border-radius: 5px; 
                        text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.1);
                    }}
                    .image-container img {{ 
                        max-width: 100%; max-height: 80vh; 
                        border-radius: 5px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                    }}
                    .download-btn {{
                        background: #28a745; color: white; padding: 8px 16px;
                        border: none; border-radius: 4px; cursor: pointer;
                        text-decoration: none; display: inline-block; margin-left: 10px;
                    }}
                    .download-btn:hover {{ background: #218838; color: white; }}
                </style>
            </head>
            <body>
                <div class="header">
                    <h1>{icon} {current_path.name}</h1>
                    <a href="/logs/{current_path.parent.relative_to(LOGS_DIR) if current_path.parent != LOGS_DIR else ""}" class="back-link">← Back to logs</a>
                    <a href="/logs/{current_path.relative_to(LOGS_DIR)}?download=1" class="download-btn">Download</a>
                </div>
                <div class="image-container">
                    <img src="/logs/{current_path.relative_to(LOGS_DIR)}?download=1" alt="{current_path.name}">
                </div>
            </body>
            </html>
            """
            return HTMLResponse(content=html_content)

        # For text files, try to display with HTML wrapper unless download is requested
        if not is_download_only and not download:
            try:
                with open(current_path, "r", encoding="utf-8") as f:
                    file_content = f.read()

                # Get appropriate icon for file type
                icon = "📄"
                if current_path.suffix.lower() in {".log", ".txt"}:
                    icon = "📄"
                elif current_path.suffix.lower() in {
                    ".py",
                    ".js",
                    ".ts",
                    ".html",
                    ".css",
                    ".json",
                    ".xml",
                    ".yaml",
                    ".yml",
                }:
                    icon = "📝"
                elif current_path.suffix.lower() in {".md"}:
                    icon = "📖"
                elif current_path.suffix.lower() in {".csv"}:
                    icon = "📊"

                html_content = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>{current_path.name}</title>
                    <style>
                        body {{ font-family: 'Courier New', monospace; margin: 20px; background: #f8f9fa; }}
                        .header {{ background: white; padding: 15px; border-radius: 5px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
                        .header h1 {{ margin: 0; color: #333; }}
                        .back-link {{ color: #007bff; text-decoration: none; }}
                        .back-link:hover {{ text-decoration: underline; }}
                        .file-content {{ 
                            background: #1e1e1e; color: #f8f8f2; padding: 20px; 
                            border-radius: 5px; white-space: pre-wrap; 
                            font-size: 14px; line-height: 1.4;
                            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
                            max-height: 80vh; overflow-y: auto;
                        }}
                        .download-btn {{
                            background: #28a745; color: white; padding: 8px 16px;
                            border: none; border-radius: 4px; cursor: pointer;
                            text-decoration: none; display: inline-block; margin-left: 10px;
                        }}
                        .download-btn:hover {{ background: #218838; color: white; }}
                    </style>
                </head>
                <body>
                    <div class="header">
                        <h1>{icon} {current_path.name}</h1>
                        <a href="/logs/{current_path.parent.relative_to(LOGS_DIR) if current_path.parent != LOGS_DIR else ""}" class="back-link">← Back to logs</a>
                        <a href="/logs/{current_path.relative_to(LOGS_DIR)}?download=1" class="download-btn">Download</a>
                    </div>
                    <div class="file-content">{file_content}</div>
                </body>
                </html>
                """
                return HTMLResponse(content=html_content)
            except UnicodeDecodeError:
                # If file can't be read as text, treat as binary
                is_download_only = True

        # For binary files or when download is requested, serve as download
        if is_download_only or download:
            return FileResponse(
                path=str(current_path), filename=current_path.name, media_type=mime_type
            )
        else:
            # Fallback for inline display without wrapper (shouldn't normally reach here)
            return FileResponse(path=str(current_path), media_type=mime_type)

    # It's a directory, show the browser
    html_content = get_file_browser_html(current_path, str(request.url))
    return HTMLResponse(content=html_content)


@router.delete("/delete/{filepath:path}", include_in_schema=False)
async def delete_file(filepath: str):
    """Delete a specific file."""
    file_path = LOGS_DIR / filepath

    # Security check
    try:
        file_path.resolve().relative_to(LOGS_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file path")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="Not a file")

    file_path.unlink()
    return JSONResponse({"message": f"File deleted successfully"})


@router.delete("/delete-folder/{folderpath:path}", include_in_schema=False)
async def delete_folder(folderpath: str):
    """Delete a folder and all its contents."""
    import shutil

    folder_path = LOGS_DIR / folderpath

    # Security check
    try:
        folder_path.resolve().relative_to(LOGS_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid folder path")

    if not folder_path.exists():
        raise HTTPException(status_code=404, detail="Folder not found")

    if not folder_path.is_dir():
        raise HTTPException(status_code=400, detail="Not a folder")

    shutil.rmtree(folder_path)
    return JSONResponse({"message": f"Folder deleted successfully"})


@router.delete("/delete-all", include_in_schema=False)
async def delete_all_items(request: Request):
    """Delete all files and folders in the current directory."""
    import shutil

    request_data = await request.json()
    path = request_data.get("path", "/logs").replace("/logs", "").lstrip("/")

    current_path = LOGS_DIR / path if path else LOGS_DIR

    # Security check
    try:
        current_path.resolve().relative_to(LOGS_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not current_path.exists():
        return JSONResponse({"message": "Directory does not exist"})

    deleted_files = 0
    deleted_folders = 0

    for item in current_path.iterdir():
        if item.is_file():
            item.unlink()
            deleted_files += 1
        elif item.is_dir():
            shutil.rmtree(item)
            deleted_folders += 1

    total_deleted = deleted_files + deleted_folders
    message = f"Deleted {deleted_files} files"
    if deleted_folders > 0:
        message += f" and {deleted_folders} folders"

    return JSONResponse({"message": message})
