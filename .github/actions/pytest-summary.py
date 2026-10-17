#!/usr/bin/env python3
import json
import xml.etree.ElementTree as ET

tree = ET.parse('pytest-results.xml')
root = tree.getroot()

# Extract summary
summary = {
    'tests': root.attrib.get('tests', 0),
    'failures': root.attrib.get('failures', 0),
    'errors': root.attrib.get('errors', 0),
    'skipped': root.attrib.get('skipped', 0),
    'hostname': root.attrib.get('hostname', 'unknown'),
    'timestamp': root.attrib.get('timestamp', ''),
}

# Extract failure/error details
failures_errors = []
for testcase in root.findall('.//testcase'):
    name = testcase.get('name')
    classname = testcase.get('classname', '')
    result = testcase.get('result', 'unknown')

    failure = testcase.find('failure')
    error = testcase.find('error')

    msg = ''
    if failure is not None:
        msg = failure.get('message', '')
        msg += '\n' + (failure.text or '').strip()
    elif error is not None:
        msg = error.get('message', '')
        msg += '\n' + (error.text or '').strip()

    failures_errors.append({
        'name': name,
        'classname': classname,
        'result': result,
        'message': msg[:500] if msg else ''
    })

result = {
    'summary': summary,
    'failures_errors': failures_errors
}

with open('test-summary.json', 'w') as f:
    json.dump(result, f, indent=2)
