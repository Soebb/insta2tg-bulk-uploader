package com.soebb.warpcryptotest;

import android.app.Activity;
import android.os.Bundle;
import android.graphics.Typeface;
import android.view.Gravity;
import android.widget.ScrollView;
import android.widget.TextView;

import com.chaquo.python.PyException;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

public class MainActivity extends Activity {
    private TextView output;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        output = new TextView(this);
        output.setTextSize(16);
        output.setTypeface(Typeface.MONOSPACE);
        output.setPadding(28, 36, 28, 36);
        output.setGravity(Gravity.TOP);
        output.setText("Starting Python 3.11...\n");

        ScrollView scroll = new ScrollView(this);
        scroll.addView(output);
        setContentView(scroll);

        new Thread(this::runTest).start();
    }

    private void runTest() {
        try {
            if (!Python.isStarted()) {
                Python.start(new AndroidPlatform(this));
            }
            String result = Python.getInstance()
                    .getModule("warpcrypto_test")
                    .callAttr("run_tests")
                    .toString();
            runOnUiThread(() -> output.setText(result));
        } catch (PyException e) {
            String error = "WARPCRYPTO TEST FAILED\n\n" + e;
            runOnUiThread(() -> output.setText(error));
        } catch (Throwable e) {
            String error = "APP ERROR\n\n" + e;
            runOnUiThread(() -> output.setText(error));
        }
    }
}
