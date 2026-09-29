# Windows Driver Toolbox

يحتوي المشروع على الواجهة الرئيسية والبدائل المستقلة التالية:

- `driver_toolbox.exe`: الملف الحالي، ويبقى دون تعديل.
- `driver_toolbox.py`: مصدر الملف الحالي.
- `backup_drivers.py`: أداة مستقلة لأخذ نسخة من التعريفات.
- `backup_drivers.exe`: النسخة التنفيذية المستقلة من أداة النسخ.
- `restore_drivers.py`: أداة مستقلة لاستعادة التعريفات.
- `restore_drivers.exe`: النسخة التنفيذية المستقلة من أداة الاستعادة.
- `DriverToolbox.hta`: الواجهة الرسومية الجديدة.

## مجلد العمل

تستخدم أدوات النسخ والاستعادة المجلد التالي بجانب الملف الذي يتم تشغيله:

`BackupDriver`

إذا لم يكن المجلد موجودًا، يتم إنشاؤه تلقائيًا.

## بناء الأدوات البديلة

من داخل مجلد المشروع شغّل:

```cmd
python -m PyInstaller --onefile --uac-admin --name backup_drivers backup_drivers.py
python -m PyInstaller --onefile --uac-admin --name restore_drivers restore_drivers.py
```

ستظهر الملفات الجديدة داخل مجلد `dist`. يمكن وضع `backup_drivers.exe` و`restore_drivers.exe` بجانب `BackupDriver` أو تشغيلهما من المجلد نفسه؛ كل أداة تحدد موقعها تلقائيًا.

## الاستخدام البديل

إذا تعذر تشغيل الواجهة الرسومية، شغّل `backup_drivers.exe` لأخذ النسخة، وبعد الفورمات شغّل `restore_drivers.exe` لاستعادتها. يحفظ برنامج الاستعادة تقريرًا تفصيليًا للتعريفات التي نجحت أو فشلت.
