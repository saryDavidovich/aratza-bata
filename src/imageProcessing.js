const sharp = require('sharp');
const fs = require('fs');
const path = require('path');

// Gmail (ומספקי מייל אחרים) חותכים הודעה שלמה אם היא עוברת בערך 102KB -
// ואז שאר המייל, כולל תמונות, פשוט לא מוצג. תמונה גולמית מהטלפון יכולה
// בקלות להיות כמה מגה-בייט. הפונקציה הזו דוחסת ומקטינה כל תמונה שמועלית
// כך שגודל ה-base64 שלה נשאר קטן מספיק שהמייל לא ייחתך, גם עם כמה מודעות
// עם תמונות באותו גיליון.

const MAX_WIDTH = 480;
const JPEG_QUALITY = 60;

async function compressUploadedImage(filePath) {
  const ext = path.extname(filePath).toLowerCase();

  try {
    if (ext === '.gif') {
      // גיף מונפש - שומרים על ההנפשה (animated: true), רק מקטינים גודל
      // אם התמונה גדולה מדי. לא ממירים פורמט כדי לא לאבד את האנימציה.
      const buffer = fs.readFileSync(filePath);
      const resized = await sharp(buffer, { animated: true })
        .resize({ width: MAX_WIDTH, withoutEnlargement: true })
        .gif()
        .toBuffer();
      fs.writeFileSync(filePath, resized);
      return filePath;
    }

    // תמונות רגילות (png/jpg/webp) - ממירים ל-JPEG דחוס, זה הכי קטן.
    // שקיפות (png) תיהפך לרקע לבן - קביל למודעות טקסט/תמונה רגילות.
    const buffer = fs.readFileSync(filePath);
    const resized = await sharp(buffer)
      .resize({ width: MAX_WIDTH, withoutEnlargement: true })
      .flatten({ background: '#ffffff' })
      .jpeg({ quality: JPEG_QUALITY })
      .toBuffer();

    // שינוי סיומת הקובץ בפועל ל-jpg, כדי שגם ה-mime type שנקבע בהמשך יהיה נכון
    const newPath = filePath.replace(/\.[^.]+$/, '.jpg');
    fs.writeFileSync(newPath, resized);
    if (newPath !== filePath) fs.unlinkSync(filePath);
    return newPath;
  } catch (err) {
    console.error('שגיאה בדחיסת תמונה, נשארת כפי שהיא:', err.message);
    return filePath;
  }
}

module.exports = { compressUploadedImage };
