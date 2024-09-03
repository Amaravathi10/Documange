import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash, jsonify, json
from werkzeug.utils import secure_filename
import re
from tika import parser


app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Required for flashing messages

# Configure upload folder and allowed extensions
UPLOAD_FOLDER = 'uploads/'
ALLOWED_EXTENSIONS = {'doc', 'docx'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Database setup
DATABASE = 'resumes.db'

def get_db():
    """Connect to the SQLite database."""
    conn = sqlite3.connect(DATABASE)
    return conn

def create_table():
    """Create the resumes table with the name column if it doesn't exist."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS resumes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            content TEXT NOT NULL,
            name TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def allowed_file(filename):
    """Check if the uploaded file has an allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def normalize_text(text):
    """Normalize text by converting to lowercase and removing non-alphanumeric characters."""
    text = re.sub(r'[^a-zA-Z0-9\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip().lower()
    return text

def extract_name_from_filename(filename):
    """Extract the name part from the filename more flexibly."""
    # Improved regex pattern to handle diverse naming formats
    match = re.search(r'_(\w+(?:_\w+)*)_', filename)
    if match:
        # Splitting by double underscores to clean any trailing job titles or locations
        name = match.group(1).split('__')[0]
        return name.strip()
    else:
        return None


def is_resume_unique(name):
    """Check if the resume with the extracted name is already in the database."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM resumes WHERE name = ?", (name,))
    result = cursor.fetchone()
    conn.close()
    return result is None

def save_resume_to_db(filename, content, name):
    """Save the resume content and name to the database."""
    conn = get_db()
    cursor = conn.cursor()
    normalized_content = normalize_text(content)
    cursor.execute('INSERT INTO resumes (filename, content, name) VALUES (?, ?, ?)', (filename, normalized_content, name))
    conn.commit()
    conn.close()

def convert_doc_to_text(filepath):
    """Convert a DOC or DOCX file to plain text using Apache Tika."""
    try:
        parsed = parser.from_file(filepath)
        return parsed['content'].strip()
    except Exception as e:
        print(f"Error processing file {filepath}: {e}")
        return None

@app.route('/')
def index():
    """Render the index page showing the list of resumes in the database."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT filename FROM resumes")
    resumes = cursor.fetchall()
    conn.close()
    return render_template('index.html', resumes=resumes)

@app.route('/upload', methods=['POST'])
def upload_files():
    """Handle the uploading and processing of resume files."""
    if 'files[]' not in request.files:
        flash('No files part in the request')
        return redirect(request.url)
    
    files = request.files.getlist('files[]')
    if not files:
        flash('No files selected for uploading')
        return redirect(request.url)

    error_files = []  # List to keep track of files that failed to extract content
    for file in files:
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            # Extract the name from the filename
            name = extract_name_from_filename(filename)
            if not name:
                flash(f"Unable to extract name from {filename}.")
                continue

            # Check if the resume is unique based on the extracted name
            if not is_resume_unique(name):
                flash(f"Resume with the name '{name}' already exists in the database.")
                os.remove(filepath)  # Clean up the saved file
                continue

            # Extract text from the Word document
            text_content = convert_doc_to_text(filepath)

            if text_content and text_content.strip():
                # Save the text content and name to the database
                save_resume_to_db(filename, text_content, name)
            else:
                error_files.append(filename)
        else:
            flash(f"{file.filename} has an unsupported file extension.")
    
    if error_files:
        flash(f"Failed to extract content from the following files: {', '.join(error_files)}")
    else:
        flash("All files uploaded and processed successfully.")
    
    return redirect(url_for('index'))

@app.route('/search', methods=['GET', 'POST'])
def search():
    """Search for resumes by a specific skill."""
    if request.method == 'POST':
        skill = request.form['skill'].lower()
        conn = get_db()
        cursor = conn.cursor()
        skill = normalize_text(skill)
        cursor.execute("SELECT filename FROM resumes WHERE content LIKE ?", ('%' + skill + '%',))
        results = cursor.fetchall()
        conn.close()
        return render_template('search.html', results=results, skill=skill)
    return render_template('search.html')

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serve uploaded files."""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/view_database')
def view_database():
    """Render a page showing the list of resumes in the database."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT filename FROM resumes")
    resumes = cursor.fetchall()
    conn.close()
    return render_template('view_database.html', resumes=resumes)



@app.route('/delete_resume/<filename>', methods=['POST'])
def delete_resume(filename):
    """Delete the selected resume from the database and file system after password confirmation."""
    password = request.form.get('password')
    correct_password = 'Logisoft@2016'  # Set your desired password here
    
    # Check if the entered password is correct
    if password != correct_password:
        flash('Incorrect password. Resume not deleted.')
        return redirect(url_for('view_database'))
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Delete the resume from the database
    cursor.execute("DELETE FROM resumes WHERE filename = ?", (filename,))
    conn.commit()
    conn.close()

    # Remove the file from the uploads directory
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if os.path.exists(file_path):
        os.remove(file_path)

    flash(f"Resume '{filename}' has been deleted successfully.")
    return redirect(url_for('view_database'))


@app.route('/bulk_delete', methods=['POST'])
def bulk_delete():
    filenames = request.form.get('filenames')
    password = request.form.get('password')
    
    # Validate password
    if not validate_password(password):
        flash('Incorrect password.')
        return redirect(url_for('view_database'))

    if filenames:
        filenames = json.loads(filenames)  # Convert JSON string back to list
        success = True
        errors = []
        
        for filename in filenames:
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    # Optionally, remove from the database as well
                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM resumes WHERE filename = ?", (filename,))
                    conn.commit()
                    conn.close()
                except Exception as e:
                    errors.append(filename)
                    success = False
        
        if success:
            flash('Selected resumes deleted successfully.')
        else:
            flash(f'Failed to delete some resumes: {", ".join(errors)}')
    else:
        flash('No resumes selected for deletion.')

    return redirect(url_for('view_database'))

def validate_password(password):
    # Your password validation logic here
    return password == 'Logisoft@2016'





@app.route('/view_resume/<filename>/<keyword>')
def view_resume(filename, keyword):
    """Display the resume content with highlighted keywords."""
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

    # Convert the document to text
    text_content = convert_doc_to_text(filepath)

    if not text_content:
        flash(f"Unable to extract content from {filename}.")
        return redirect(url_for('search'))

    # Highlight the searched keyword in the content
    highlighted_content = highlight_keywords(text_content, keyword)

    return render_template('view_resume.html', filename=filename, content=highlighted_content, keyword=keyword)


def highlight_keywords(text, keyword):
    """Highlight the searched keyword within the text content."""
    # Use regex to make keyword highlighting case-insensitive
    highlighted_text = re.sub(
        f"({re.escape(keyword)})",
        r'<mark>\1</mark>',
        text,
        flags=re.IGNORECASE
    )
    return highlighted_text


if __name__ == '__main__':
    # Ensure the uploads directory exists
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
    
    # Create the database and table if they don't exist
    create_table()
    
    app.run(debug=True)