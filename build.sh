#!/bin/bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
ICON_SRC="/Users/lanx/Downloads/IMG_6483.JPG"
ICONSET_DIR="$PROJECT_DIR/ios-app/ios/App/App/Assets.xcassets/AppIcon.appiconset"
WWW_DIR="$PROJECT_DIR/ios-app/www"

echo "🔨 Building 不简单Life..."

# Step 0: Sync web assets (Capacitor)
echo "→ Syncing web assets..."
cd "$PROJECT_DIR/ios-app" && npx cap copy ios 2>&1 | tail -2

# Step 1: Generate icons from Downloads image
if [ -f "$ICON_SRC" ]; then
  echo "→ Generating app icons from $(basename "$ICON_SRC")..."

  # Convert to 1024x1024 base icon (force .png extension)
  sips -z 1024 1024 "$ICON_SRC" --out "$ICONSET_DIR/icon-1024.png" -s format png > /dev/null

  # iOS icon sizes: name=px
  gen_icon() { sips -z "$2" "$2" "$ICONSET_DIR/icon-1024.png" --out "$ICONSET_DIR/$1.png" > /dev/null; }
  gen_icon "icon-20"       20
  gen_icon "icon-20@2x"    40
  gen_icon "icon-20@3x"    60
  gen_icon "icon-29"       29
  gen_icon "icon-29@2x"    58
  gen_icon "icon-29@3x"    87
  gen_icon "icon-40"       40
  gen_icon "icon-40@2x"    80
  gen_icon "icon-40@3x"    120
  gen_icon "icon-60@2x"    120
  gen_icon "icon-60@3x"    180
  gen_icon "icon-76"       76
  gen_icon "icon-76@2x"    152
  gen_icon "icon-83.5@2x"  167
  gen_icon "icon-1024"     1024

  # Write Contents.json
  cat > "$ICONSET_DIR/Contents.json" << 'JSONEOF'
{
  "images" : [
    {"size" : "20x20", "idiom" : "iphone", "filename" : "icon-20@2x.png", "scale" : "2x"},
    {"size" : "20x20", "idiom" : "iphone", "filename" : "icon-20@3x.png", "scale" : "3x"},
    {"size" : "29x29", "idiom" : "iphone", "filename" : "icon-29@2x.png", "scale" : "2x"},
    {"size" : "29x29", "idiom" : "iphone", "filename" : "icon-29@3x.png", "scale" : "3x"},
    {"size" : "40x40", "idiom" : "iphone", "filename" : "icon-40@2x.png", "scale" : "2x"},
    {"size" : "40x40", "idiom" : "iphone", "filename" : "icon-40@3x.png", "scale" : "3x"},
    {"size" : "60x60", "idiom" : "iphone", "filename" : "icon-60@2x.png", "scale" : "2x"},
    {"size" : "60x60", "idiom" : "iphone", "filename" : "icon-60@3x.png", "scale" : "3x"},
    {"size" : "20x20", "idiom" : "ipad", "filename" : "icon-20.png", "scale" : "1x"},
    {"size" : "20x20", "idiom" : "ipad", "filename" : "icon-20@2x.png", "scale" : "2x"},
    {"size" : "29x29", "idiom" : "ipad", "filename" : "icon-29.png", "scale" : "1x"},
    {"size" : "29x29", "idiom" : "ipad", "filename" : "icon-29@2x.png", "scale" : "2x"},
    {"size" : "40x40", "idiom" : "ipad", "filename" : "icon-40.png", "scale" : "1x"},
    {"size" : "40x40", "idiom" : "ipad", "filename" : "icon-40@2x.png", "scale" : "2x"},
    {"size" : "76x76", "idiom" : "ipad", "filename" : "icon-76.png", "scale" : "1x"},
    {"size" : "76x76", "idiom" : "ipad", "filename" : "icon-76@2x.png", "scale" : "2x"},
    {"size" : "83.5x83.5", "idiom" : "ipad", "filename" : "icon-83.5@2x.png", "scale" : "2x"},
    {"size" : "1024x1024", "idiom" : "ios-marketing", "filename" : "icon-1024.png", "scale" : "1x"}
  ],
  "info" : {"author" : "xcode", "version" : 1}
}
JSONEOF

  echo "✓ Icons generated"
else
  echo "⚠️  $ICON_SRC not found, skipping icon generation"
fi

# Step 3: Build with Xcode
echo "→ Building for iOS Simulator..."
cd "$PROJECT_DIR/ios-app/ios/App"
xcodebuild \
  -project App.xcodeproj \
  -scheme App \
  -configuration Debug \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -derivedDataPath "$PROJECT_DIR/ios-app/build" \
  build 2>&1 | tail -20

echo ""
echo "✓ Build complete. Installing to simulator..."
xcrun simctl install booted "$PROJECT_DIR/ios-app/build/Build/Products/Debug-iphonesimulator/App.app"
xcrun simctl launch booted com.lanx.snailbooks

echo "✓ App launched on simulator"
