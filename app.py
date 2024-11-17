from flask import Flask, render_template, request, redirect, url_for, session, flash, make_response
import sqlite3
from datetime import datetime
import csv

import smtplib
import ssl
from email.message import EmailMessage

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# Database connection
def get_db():
    conn = sqlite3.connect('attendance_system.db')
    conn.row_factory = sqlite3.Row
    return conn

# Routes

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        db = get_db()

        # Check Lecturer
        user = db.execute('SELECT * FROM lecturers WHERE email = ? AND password = ?', (email, password)).fetchone()
        if user:
            session['user_id'] = user['id']
            session['role'] = 'lecturer'
            return redirect(url_for('lecturer_dashboard'))

        # Check HOD
        if email == "hod@example.com" and password == "hod_password":  # Replace with actual HOD credentials
            session['user_id'] = 0  # HOD ID
            session['role'] = 'hod'
            return redirect(url_for('hod_dashboard'))

        flash('Invalid credentials')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/mark_attendance', methods=['POST'])
def mark_attendance():
    matric_number = request.form['matric_number']
    course_name = request.form['course']
    status = request.form['status']
    date = datetime.now().strftime('%Y-%m-%d')

    db = get_db()
    student = db.execute('SELECT * FROM students WHERE matric_number = ?', (matric_number,)).fetchone()
    course = db.execute('SELECT * FROM courses WHERE course_name = ?', (course_name,)).fetchone()

    if student and course:
        db.execute('INSERT INTO attendance (student_id, course_id, date, status) VALUES (?, ?, ?, ?)',
                   (student['id'], course['course_id'], date, status))
        db.commit()
        flash('Attendance marked successfully!')
    else:
        flash('Invalid student or course')
    return redirect(url_for('home'))

@app.route('/lecturer_dashboard')
def lecturer_dashboard():
    if 'user_id' not in session or session['role'] != 'lecturer':
        return redirect(url_for('login'))

    db = get_db()
    courses = db.execute('SELECT * FROM courses WHERE lecturer_id = ?', (session['user_id'],)).fetchall()
    return render_template('lecturer_dashboard.html', courses=courses)

@app.route('/hod_dashboard')
def hod_dashboard():
    if 'user_id' not in session or session['role'] != 'hod':
        return redirect(url_for('login'))

    db = get_db()
    courses = db.execute('SELECT * FROM courses').fetchall()
    return render_template('hod_dashboard.html', courses=courses)

@app.route('/download_attendance/<int:course_id>')
def download_attendance(course_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    db = get_db()
    role = session['role']

    # Check if the user is a lecturer and ensure they are authorized to download the attendance
    if role == 'lecturer':
        course = db.execute('SELECT * FROM courses WHERE course_id = ? AND lecturer_id = ?',
                            (course_id, session['user_id'])).fetchone()
        if not course:
            flash('Unauthorized access')
            return redirect(url_for('lecturer_dashboard'))

    # Fetch the attendance data, including the number of presents per student
    attendance_records = db.execute('''
        SELECT s.matric_number, s.first_name, s.last_name,
               COUNT(CASE WHEN a.status = "Present" THEN 1 END) AS total_present
        FROM attendance a
        JOIN students s ON a.student_id = s.id
        WHERE a.course_id = ?
        GROUP BY s.id
    ''', (course_id,)).fetchall()

    # Calculate the attendance percentage for each student
    total_classes = 10  # The total number of classes (contact hours)
    data = []
    
    for record in attendance_records:
        total_present = record['total_present']
        percentage = (total_present / total_classes) * 100  # Calculate percentage of attendance
        data.append({
            'matric_number': record['matric_number'],
            'first_name': record['first_name'],
            'last_name': record['last_name'],
            'total_present': total_present,
            'percentage': f"{percentage:.2f}%"
        })

    # Generate the CSV output with the attendance and percentages
    output = []
    header = ['Matric Number', 'First Name', 'Last Name', 'Total Present', 'Percentage']
    output.append(header)

    for record in data:
        output.append([
            record['matric_number'],
            record['first_name'],
            record['last_name'],
            record['total_present'],
            record['percentage']
        ])

    # Convert data to CSV format
    csv_output = "\n".join([",".join(map(str, row)) for row in output])
    
    # Generate the response for downloading the CSV file
    response = make_response(csv_output)
    response.headers["Content-Disposition"] = f"attachment; filename=attendance_course_{course_id}.csv"
    response.mimetype = "text/csv"
    
    return response

@app.route('/view_attendance/<int:course_id>')
def view_attendance(course_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    db = get_db()

    # Fetch course details
    course = db.execute('SELECT * FROM courses WHERE course_id = ?', (course_id,)).fetchone()
    if not course:
        flash("Course not found!")
        return redirect(url_for('lecturer_dashboard'))

    # Fetch attendance details for the course
    attendance_records = db.execute('''
        SELECT s.matric_number, s.first_name, s.last_name,
               COUNT(CASE WHEN a.status = "Present" THEN 1 END) AS total_present
        FROM attendance a
        JOIN students s ON a.student_id = s.id
        WHERE a.course_id = ?
        GROUP BY s.id
    ''', (course_id,)).fetchall()

    # Calculate attendance percentage
    total_classes = 10  # Replace with the actual number of classes
    attendance_data = []
    for record in attendance_records:
        total_present = record['total_present']
        percentage = (total_present / total_classes) * 100
        attendance_data.append({
            'matric_number': record['matric_number'],
            'first_name': record['first_name'],
            'last_name': record['last_name'],
            'total_present': total_present,
            'attendance_percentage': round(percentage, 2),
        })

    return render_template('view_attendance.html', course=course, attendance=attendance_data)



@app.route('/send_low_attendance_emails/<int:course_id>', methods=['GET'])
def send_low_attendance_emails(course_id):
    db = get_db()
    query = """
    SELECT s.first_name, s.last_name, s.email AS student_email, s.parent_email,
           COUNT(CASE WHEN a.status = 'Present' THEN 1 END) AS total_present,
           (COUNT(CASE WHEN a.status = 'Present' THEN 1 END) / 10.0) * 100 AS attendance_percentage
    FROM attendance a
    JOIN students s ON a.student_id = s.id
    WHERE a.course_id = ?
    GROUP BY s.id
    HAVING attendance_percentage < 60
    """
    low_attendance_records = db.execute(query, (course_id,)).fetchall()

    for record in low_attendance_records:
        send_email_to_student_and_parent(record)

    flash('Emails sent to students and their parents for attendance below 60%.')
    return redirect(url_for('view_attendance', course_id=course_id))



def get_attendance_data(course_id):
    conn = get_db()  # Use the get_db function to establish a connection
    cursor = conn.cursor()  # Create a cursor object
    query = """
    SELECT s.matric_number, s.first_name, s.last_name, COUNT(CASE WHEN a.status = 'Present' THEN 1 END) AS total_present, 
           (COUNT(CASE WHEN a.status = 'Present' THEN 1 END) / 10.0) * 100 AS attendance_percentage
    FROM attendance a
    JOIN students s ON a.student_id = s.id
    WHERE a.course_id = ?
    GROUP BY s.id
    """
    cursor.execute(query, (course_id,))
    attendance_data = cursor.fetchall()
    conn.close()  # Close the connection
    return attendance_data

def send_email_to_student_and_parent(record):
    email_sender = 'noreplyattendance2024@gmail.com'
    email_password = 'hshw vqdu jnii mtnw'

    student_email = record['student_email']
    parent_email = record['parent_email']
    subject = 'Low Attendance Alert'
    body = f"""
    Dear {record['first_name']} {record['last_name']},

    Your current attendance for the course is {record['attendance_percentage']}%. 
    Please ensure to attend more classes to meet the minimum requirement of 70%.

    Best regards,
    Attendance System
    """

    emails_to_send = [student_email, parent_email]

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=context) as smtp:
        smtp.login(email_sender, email_password)
        for recipient in emails_to_send:
            em = EmailMessage()
            em['From'] = email_sender
            em['To'] = recipient
            em['Subject'] = subject
            em.set_content(body)
            try:
                smtp.sendmail(email_sender, recipient, em.as_string())
                print(f"Email sent to {recipient}")
            except Exception as e:
                print(f"Failed to send email to {recipient}: {e}")


    # subject = "Low Attendance Alert"
    # message = f"Dear {record['first_name']} {record['last_name']},\n\nYou have low attendance in your course. Your current attendance is {record['attendance_percentage']}%. Please attend more classes to improve your attendance.\n\nRegards,\nAttendance System"

    


# Run the app
if __name__ == '__main__':
    app.run(debug=True)
