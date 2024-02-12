import XMonad

import XMonad.Util.EZConfig
import XMonad.Util.Ungrab

import System.Random

import Data.IORef
import Data.Char

import qualified GHC.IO as IO

keyboardLayouts :: [String]
keyboardLayouts = ["us", "ru"]

currentKeyboardLayout :: IORef Int
currentKeyboardLayout = IO.unsafePerformIO $ newIORef 0
{-# NOINLINE currentKeyboardLayout #-}

main = xmonad $ def
  { modMask = mod4Mask
  , terminal = "kitty"
  , focusedBorderColor = "#ffffff"}

  `additionalKeys`
  [ ((mod4Mask, xK_b)  , spawn "brave")
  , ((shiftMask, xK_Alt_L) , do
    layout <- liftIO $ readIORef currentKeyboardLayout
    spawn $ "setxkbmap " ++ keyboardLayouts !! layout
    liftIO $ modifyIORef' currentKeyboardLayout ((`mod` length keyboardLayouts) . succ))
  , ((noModMask, xK_Print), do 
      str <- take 32 . filter isAlphaNum . randoms @Char <$> newStdGen
      unGrab <* spawn ("scrot -s /home/chell/Pictures/Screenshots/" ++ str ++ ".png"))
  , ((mod4Mask, xK_z), spawn "shutdown -h now")
  , ((mod4Mask, xK_x), spawn "reboot")
  ]