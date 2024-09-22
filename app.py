import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash, jsonify, json
from werkzeug.utils import secure_filename
import re
from tika import parser
from flask import session

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
    conn = sqlite3.connect(DATABASE,timeout=10)
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

def create_employee_table():
    """Create the employees table to store usernames and passwords."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL
        )
    ''')

    # Insert default users if they don't already exist
    default_users = [('admin', 'Logisoft@2016'),
                     ('ceo', 'Logisoft@2016'),
                     ('manager', 'Logisoft@2016'),
                     ('hr', 'Logisoft@2016')]

    cursor.executemany('''
        INSERT OR IGNORE INTO employees (username, password)
        VALUES (?, ?)
    ''', default_users)
    
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
    
def highlight_keywords(text, keywords):
    """Highlight multiple keywords within the text content."""
    for keyword in keywords:
        # Use regex to make keyword highlighting case-insensitive
        text = re.sub(
            f"({re.escape(keyword)})",
            r'<mark>\1</mark>',
            text,
            flags=re.IGNORECASE
        )
    return text

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM employees WHERE username = ? AND password = ?", (username, password))
        user = cursor.fetchone()
        conn.close()

        if user:
            session['username'] = username  # Store username in session
            flash(f"Welcome, {username}!")
            return redirect(url_for('index'))
        else:
            flash('Incorrect username or password. Please try again.')
            return redirect(url_for('login'))
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    """Log out the user and clear the session."""
    session.pop('username', None)
    flash('You have been logged out.')
    return redirect(url_for('login'))

@app.route('/')
def index():
    """Render the index page showing the list of resumes, but only after login."""
    if 'username' not in session:
        flash("Please log in to continue.")
        return redirect(url_for('login'))

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
    """Search for resumes by one or more skills."""
    if request.method == 'POST':
        skills = request.form['skill'].lower().split(',')
        skills = [normalize_text(skill.strip()) for skill in skills]  # Normalize and strip whitespace

        conn = get_db()
        cursor = conn.cursor()

        # Create a query to match all the provided skills
        # This query checks if all the skills exist in the 'content' column
        query = "SELECT filename FROM resumes WHERE " + " AND ".join(["content LIKE ?" for _ in skills])
        cursor.execute(query, tuple(f'%{skill}%' for skill in skills))
        results = cursor.fetchall()

        conn.close()
        return render_template('search.html', results=results, skills=request.form['skill'])
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

@app.route('/manage_credentials', methods=['GET', 'POST'])
def manage_credentials():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == 'Logisoft@2016':
            # Fetch employee credentials
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM employees')
            employees = cursor.fetchall()
            conn.close()
            return render_template('manage_credentials.html', employees=employees)
        else:
            flash('Invalid password. Please try again.', 'error')
            return redirect(url_for('index'))
    else:
        # If it's a GET request, show the manage credentials page
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM employees')
        employees = cursor.fetchall()
        conn.close()
        return render_template('manage_credentials.html', employees=employees)

@app.route('/update_credentials', methods=['POST'])
def update_credentials():
    """Handle adding, deleting, and updating credentials."""
    action = request.form['action']
    conn = get_db()
    cursor = conn.cursor()

    if action == 'add':
        username = request.form['username']
        password = request.form['password']
        cursor.execute("INSERT INTO employees (username, password) VALUES (?, ?)", (username, password))
        flash('User added successfully.')

    elif action == 'delete':
        employee_id = request.form['employee_id']
        cursor.execute("DELETE FROM employees WHERE id = ?", (employee_id,))
        flash('User deleted successfully.')

    elif action == 'update':
        employee_id = request.form['employee_id']
        new_password = request.form['password']
        cursor.execute("UPDATE employees SET password = ? WHERE id = ?", (new_password, employee_id))
        flash('Password updated successfully.')

    conn.commit()
    conn.close()
    
    return redirect(url_for('manage_credentials'))

# Route to validate password for managing credentials
@app.route('/validate_password', methods=['POST'])
def validate_password():
    password = request.form['password']
    if password == 'Logisoft@2016':  # Correct password
        return redirect(url_for('manage_credentials'))
    else:
        flash('Invalid password. Please try again.')
        return redirect(url_for('index'))

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

@app.route('/add_employee', methods=['POST'])
def add_employee():
    username = request.form['username']
    password = request.form['password']
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO employees (username, password) VALUES (?, ?)", (username, password))
        conn.commit()
        flash('Employee added successfully.')
    except sqlite3.IntegrityError:
        flash('Username already exists.')
    finally:
        conn.close()
    return redirect(url_for('manage_credentials'))  # Adjust redirect as necessary

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

@app.route('/view_word_document/<filename>')
def view_word_document(filename):
    """View the Word document directly in the browser."""
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    # Convert the Word document to HTML using Apache Tika
    try:
        parsed = parser.from_file(filepath)
        content = parsed.get('content')
        if not content:
            flash(f"Unable to extract content from {filename}.")
            return redirect(url_for('view_database'))
        
        # Remove leading and trailing whitespace from each line
        lines = content.splitlines()
        
        # Remove blank lines from the top
        while lines and not lines[0].strip():
            lines.pop(0)
        
        cleaned_content = "\n".join([line.strip() for line in lines])
        
        # Convert the text content into HTML (basic conversion)
        html_content = "<html><body>" + cleaned_content.replace("\n", "<br>") + "</body></html>"
        
        return html_content
    except Exception as e:
        flash(f"Error processing file {filename}: {e}")
        return redirect(url_for('view_database'))



@app.route('/view_resume/<filename>/<keywords>')
def view_resume(filename, keywords):
    """Display the resume content with highlighted keywords."""
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

    # Convert the document to text
    text_content = convert_doc_to_text(filepath)

    if not text_content:
        flash(f"Unable to extract content from {filename}.")
        return redirect(url_for('search'))

    # Split the keywords and highlight them
    keywords = [normalize_text(kw.strip()) for kw in keywords.split(',')]
    highlighted_content = highlight_keywords(text_content, keywords)

    return render_template('view_resume.html', filename=filename, content=highlighted_content, keywords=keywords)

if __name__ == '__main__':
    # Ensure the uploads directory exists
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
    
    # Create the database and tables if they don't exist
    create_table()
    create_employee_table()
    
    app.run(debug=True)
