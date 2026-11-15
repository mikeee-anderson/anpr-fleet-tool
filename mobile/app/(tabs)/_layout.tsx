import { Stack } from 'expo-router';
import React from 'react';

// A single scanner screen: a stack without a header instead of a one-tab bar,
// so the screen's own bottom action bar sits at the bottom edge
export default function ScannerLayout() {
  return <Stack screenOptions={{ headerShown: false }} />;
}
