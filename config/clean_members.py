import json
import re

# Load data
with open('member_search_results.json', encoding='utf-8') as f:
    data = json.load(f)

def has_surname(name):
    # Remove common prefixes and split
    name = name.strip()
    if not name:
        return False
    # Remove Arabic prefixes
    name = re.sub(r'^(ا/|ا د/|ا د|د/|د|م |ا |ا\s)', '', name).strip()
    # Count words
    return len(name.split()) > 1

def valid_phone(phone):
    # Extract all 11-digit numbers
    numbers = re.findall(r'\b01\d{9}\b', phone)
    return bool(numbers)

def is_doctor(entry):
    job = (entry.get('job') or '').strip()
    name = (entry.get('name') or '').strip()
    return any(x in job or x in name for x in ['دكتور', 'دكتوره', 'د/', 'د '])

def is_bank(entry):
    job = (entry.get('job') or '').strip()
    workplace = (entry.get('workplace') or '').strip()
    return 'بنك' in job or 'بنك' in workplace

def is_student(entry):
    job = (entry.get('job') or '').strip()
    workplace = (entry.get('workplace') or '').strip()
    return any(x in job or x in workplace for x in ['طالب', 'طالبه', 'student'])

cleaned = []
missing = []
doctors = []
bankers = []
students = []
others = []

for entry in data:
    name = (entry.get('name') or '').strip()
    phone = (entry.get('telephone') or '').replace(' ', '')
    # Remove if name missing or only one name
    if not name or not has_surname(name):
        continue
    # Check for missing/invalid data
    if not valid_phone(phone) or any(not (entry.get(k) or '').strip() for k in ['work', 'job', 'workplace']):
        missing.append(entry)
        continue
    # Categorize
    if is_doctor(entry):
        doctors.append(entry)
    elif is_bank(entry):
        bankers.append(entry)
    elif is_student(entry):
        students.append(entry)
    else:
        others.append(entry)

# Output sections
sections = [
    ('Doctors', doctors),
    ('Bank Workers', bankers),
    ('Students', students),
    ('Other', others),
    ('Has Some Data Missing', missing)
]

def print_section(title, entries):
    print(f'\n===== {title} ({len(entries)}) =====')
    for e in entries:
        name = (e.get('name') or '').strip()
        job = (e.get('job') or '').strip()
        workplace = (e.get('workplace') or '').strip()
        phone = (e.get('telephone') or '').strip()
        print(f"Name: {name} | Job: {job} | Workplace: {workplace} | Phone: {phone}")

if __name__ == '__main__':
    # Save output as cleaned_members.json
    output = {
        'Doctors': doctors,
        'Bank Workers': bankers,
        'Students': students,
        'Other': others,
        'Has Some Data Missing': missing
    }
    with open('cleaned_members.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print('Saved cleaned data to cleaned_members.json')
